from __future__ import annotations

import datetime
import os
import socket
import sys
import threading
import time
from pathlib import Path

import serial
from serial.tools import list_ports

import algorithm

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
CSV_DIR = APP_DIR / "CSV_file"

TCP_TEST = "b0a503ff41aa"
TCP_STOP = "b0a500bf40aa"
TCP_STOP_HOME = "b0a504be83aa"
TCP_CLOSE_SEQ = ["b0a504be83aa", "b0a500bf40aa", "b0a5323e95aa"]

TCP_INIT = [
    "b1a5000001000000000032000000000a00000000050000000000000000000000000000000000db6faa",
    "b2a50100000000000a000000000001016100000000000100000000000000006000008beaaa",
    "b3a5010300000001016104000000010000000000000000000000000a0100006200005a09aa",
    "e1a50000636363000000010000000000000000000000000000000000000000000000b0bbaa",
    "e2a50000636363000000010000000000000000000000000000000000000000000000b0bbaa",
    "e0a514000000ffffffffff0000000000000000000000ffffffffff0000000001000a3ba9aa",
    "f5a5ffffffffff00000000000000000000000200000000050000000000000000000000000000cd3aaa",
    "f7a50000001501000000045d00000000000a0000001e000000003200000000000a0000001e000000003200000000000a0000001e000000003200000000000a000000000a000000000a00593eaa",
    "b8a50000001e000000000500010000000000000000000000000000000000000000007e0eaa",
    "b9a50000000019000002020200000000320000000000000000000000000a00000000000000000000050001000000000a000000010101000014000001000000000000050000000001000004dff2aa",
]

TCP_PRESSURE = {
    50: [
        "b2a501000000000014000000000005095500000000000100000000000000006000009e31aa",
        "b3a5010300000005095504000000010000000000000000000000000a010000620000c427aa",
    ],
    100: [
        "b2a50100000000001400000000000a1347000000000001000000000000000060000030a8aa",
        "b3a501030000000a134704000000010000000000000000000000000a0100006200009624aa",
    ],
    150: [
        "b2a50100000000001400000000000f1d390000000000010000000000000000600000ee24aa",
        "b3a501030000000f1d3904000000010000000000000000000000000a010000620000b725aa",
    ],
    200: [
        "b2a501000000000014000000000014272b0000000000010000000000000000600000f86daa",
        "b3a5010300000014272b04000000010000000000000000000000000a010000620000cebdaa",
    ],
    250: [
        "b2a50100000000000a0000000000182f1f000000212101000000000000000100000051ddaa",
        "b3a50103000000182f1f00000000181a5c000000212100000000000a010001000000b2a0aa",
    ],
}


def _b(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


class SerialFrameParser:
    """固件上行帧（见 gap_nrf52832_prj user_adc.c / main.c）：
    数据帧 26B：包号[0..3] + 0x51[4] + 0xA0[5] + 8×uint16 BE[6..21] + 校验[22] + 0xB5[23] + 温度[24..25]
    参数帧：00 00 00 00 + len[4] + 0xB0[5] + len 字节 ASCII
    名称帧：同参数帧，类型 0xC0
    """
    DATA_LEN = 26

    def __init__(self):
        self.buf = bytearray()

    def _find_frame(self):
        buf = self.buf
        for idx in range(5, len(buf)):
            b = buf[idx]
            if b == 0xA0 and buf[idx - 1] == 0x51:
                return idx - 5, 0xA0
            if b in (0xB0, 0xC0) and buf[idx - 5:idx - 1] == b"\x00\x00\x00\x00":
                return idx - 5, b
        return None

    def feed(self, data: bytes):
        self.buf.extend(data)
        events = []
        while True:
            hit = self._find_frame()
            if hit is None:
                break
            start, typ = hit
            if typ == 0xA0:
                if len(self.buf) < start + self.DATA_LEN:
                    break
                if self.buf[start + 23] != 0xB5:
                    del self.buf[: start + 2]
                    continue
                vals = [(self.buf[start + 6 + 2 * k] << 8) | self.buf[start + 7 + 2 * k]
                        for k in range(8)]
                events.append(("channels", vals))
                del self.buf[: start + self.DATA_LEN]
            else:
                plen = self.buf[start + 4]
                total = 6 + plen
                if len(self.buf) < start + total:
                    break
                payload = bytes(self.buf[start + 6: start + total])
                events.append(("params" if typ == 0xB0 else "name", payload))
                del self.buf[: start + total]
        if len(self.buf) > 4096:
            del self.buf[:-64]
        return events


class LinkState:
    def __init__(self):
        self.lock = threading.RLock()
        self.serial_open = False
        self.serial_port = ""
        self.tcp_connected = False
        self.pressure = 0.0
        self.channels = [0] * 8
        self.left = 0.0
        self.right = 0.0
        self.verify = {"left": {}, "right": {}}
        self.name = ""
        self.log_active = False
        self.log_path = ""
        self.last_msg = ""
        self.serial_rx = 0
        self.active_side = 1
        self.zero = None
        self.frame_count = 0


class SerialLink:
    def __init__(self, state: LinkState):
        self.st = state
        self.ser: serial.Serial | None = None
        self.parser = SerialFrameParser()
        self.running = False
        self.thread: threading.Thread | None = None

    def open(self, port: str):
        self.close()
        self.ser = serial.Serial(port=port, baudrate=115200, bytesize=8, parity="N",
                                 stopbits=1, timeout=0.05, write_timeout=0.5)
        self.parser = SerialFrameParser()
        with self.st.lock:
            self.st.serial_open = True
            self.st.serial_port = port
            self.st.frame_count = 0
            self.st.zero = None
            self.st.last_msg = f"串口已打开 {port}"
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def close(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        self.thread = None
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        with self.st.lock:
            self.st.serial_open = False

    def write(self, data: bytes):
        if self.ser is None or not self.ser.is_open:
            raise RuntimeError("串口未连接")
        self.ser.write(data)

    def _reader(self):
        while self.running and self.ser is not None:
            try:
                data = self.ser.read(64)
            except Exception as exc:
                with self.st.lock:
                    self.st.last_msg = f"串口读取失败: {exc}"
                break
            if not data:
                continue
            for kind, payload in self.parser.feed(data):
                self._handle(kind, payload)

    @staticmethod
    def _side_pressure(vals, zero, coeff):
        try:
            a = float(coeff.get("A", 0) or 0)
            b = float(coeff.get("B", 0) or 0)
            c = float(coeff.get("C", 0) or 0)
            d = float(coeff.get("D", 0) or 0)
            e = float(coeff.get("E", 0) or 0)
            f = float(coeff.get("F", 0) or 0)
        except (TypeError, ValueError):
            a = b = c = d = e = f = 0.0
        lin = (a * (vals[0] - zero[0]) + b * (vals[1] - zero[1])
               + c * (vals[2] - zero[2]) + d * (vals[3] - zero[3]))
        return lin - (e * lin * lin + f * lin)

    def _handle(self, kind, payload):
        with self.st.lock:
            if kind == "channels":
                vals = list(payload)
                self.st.channels = vals
                self.st.frame_count += 1
                if self.st.frame_count == 5:
                    self.st.zero = vals[:]
                zero = self.st.zero or [0] * 8
                self.st.left = self._side_pressure(vals[:4], zero[:4], self.st.verify["left"])
                self.st.right = self._side_pressure(vals[4:], zero[4:], self.st.verify["right"])
                self.st.serial_rx += 1
            elif kind == "params":
                text = payload.decode("latin-1", errors="replace")
                parts = text.replace("\r", " ").split()
                if len(parts) >= 6:
                    keys = ["A", "B", "C", "D", "E", "F"]
                    values = {}
                    for key, token in zip(keys, parts):
                        try:
                            values[key] = float(token)
                        except ValueError:
                            values[key] = token
                    side = "left" if self.st.active_side == 1 else "right"
                    self.st.verify[side] = values
                    self.st.last_msg = f"{'左' if side == 'left' else '右'}侧参数: " + " ".join(parts[:6])
            elif kind == "name":
                self.st.name = payload.decode("latin-1", errors="replace").strip()
                self.st.last_msg = f"名称: {self.st.name}"


class TcpLink:
    def __init__(self, state: LinkState):
        self.st = state
        self.sock: socket.socket | None = None
        self.running = False
        self.reader = None
        self.beat = None

    def open(self, ip: str, port: int = 8):
        self.close(with_shutdown=False)
        sock = socket.create_connection((ip, port), timeout=2.0)
        sock.settimeout(0.2)
        self.sock = sock
        self.running = True
        with self.st.lock:
            self.st.tcp_connected = True
            self.st.last_msg = f"压力机已连接 {ip}:{port}"
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.beat = threading.Thread(target=self._beat_loop, daemon=True)
        self.reader.start()
        self.beat.start()

    def close(self, with_shutdown: bool = True):
        was_open = self.running
        self.running = False
        if was_open and with_shutdown:
            for frame in TCP_CLOSE_SEQ:
                try:
                    self.send_bytes(_b(frame))
                except Exception:
                    pass
                time.sleep(0.05)
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        with self.st.lock:
            self.st.tcp_connected = False

    def send_bytes(self, data: bytes):
        if self.sock is None:
            raise RuntimeError("压力机未连接")
        self.sock.sendall(data)

    def send_hex(self, hexstr: str):
        self.send_bytes(_b(hexstr))

    def _read_loop(self):
        while self.running and self.sock is not None:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except Exception as exc:
                with self.st.lock:
                    self.st.last_msg = f"压力机读取失败: {exc}"
                break
            if not data:
                break
            if len(data) >= 7:
                raw = data[4] + data[5] / 100 + data[6] / 10000
                with self.st.lock:
                    self.st.pressure = raw * 9.79
        with self.st.lock:
            self.st.tcp_connected = False

    def _beat_loop(self):
        while self.running:
            try:
                self.send_bytes(b"\x65")
            except Exception:
                break
            time.sleep(0.5)


class CsvLogger:
    def __init__(self, state: LinkState):
        self.st = state
        self.file = None
        self.running = False
        self.thread = None
        self.stop_event = threading.Event()

    def start(self) -> str:
        self.stop()
        CSV_DIR.mkdir(parents=True, exist_ok=True)
        name = "JJ-DbToExcel" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".csv"
        path = CSV_DIR / name
        self.file = open(path, "a", encoding="utf-8", newline="")
        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        with self.st.lock:
            self.st.log_active = True
            self.st.log_path = str(path)
            self.st.last_msg = f"开始导出 {path}"
        return str(path)

    def stop(self):
        self.running = False
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        self.thread = None
        if self.file is not None:
            try:
                self.file.close()
            except Exception:
                pass
        self.file = None
        with self.st.lock:
            self.st.log_active = False

    def _loop(self):
        while self.running:
            if self.stop_event.wait(0.1):
                break
            with self.st.lock:
                channels = list(self.st.channels)
                pressure = self.st.pressure
                left = int(self.st.left)
                right = int(self.st.right)
            if not all(channels):
                continue
            line = ",".join([f"{pressure:.6g}"] + [str(v) for v in channels] + [str(left), str(right)]) + "\r\n"
            try:
                self.file.write(line)
                self.file.flush()
            except Exception as exc:
                with self.st.lock:
                    self.st.last_msg = f"写 CSV 失败: {exc}"
                break


class Api:
    def __init__(self):
        self._st = LinkState()
        self._serial = SerialLink(self._st)
        self._tcp = TcpLink(self._st)
        self._logger = CsvLogger(self._st)
        self._window = None
        self._seq_lock = threading.Lock()
        self._seq_cancel = threading.Event()
        self._io_lock = threading.Lock()

    def bind(self, window):
        self._window = window

    def _msg(self, text: str):
        with self._st.lock:
            self._st.last_msg = text

    def get_state(self):
        with self._st.lock:
            return {
                "serial_open": self._st.serial_open,
                "serial_port": self._st.serial_port,
                "tcp_connected": self._st.tcp_connected,
                "pressure": self._st.pressure,
                "channels": list(self._st.channels),
                "left": self._st.left,
                "right": self._st.right,
                "verify": {k: dict(v) for k, v in self._st.verify.items()},
                "name": self._st.name,
                "log_active": self._st.log_active,
                "log_path": self._st.log_path,
                "last_msg": self._st.last_msg,
                "serial_rx": self._st.serial_rx,
                "active_side": self._st.active_side,
                "zero": list(self._st.zero or []),
            }

    def list_ports(self):
        return [{"device": p.device, "description": p.description} for p in list_ports.comports()]

    def fit_all(self, files, center_idx):
        try:
            out = algorithm.fit_all(files, int(center_idx), 1)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "schemes": out}

    def get_k(self, rows, clum):
        try:
            return {"ok": True, "value": algorithm.k_one_rows(rows, clum)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def connect_serial(self, port: str):
        try:
            self._serial.open(port)
            return {"ok": True}
        except Exception as exc:
            self._msg(f"串口打开失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def disconnect_serial(self):
        self._serial.close()
        self._msg("串口已断开")
        return {"ok": True}

    def zero_now(self):
        """把当前通道帧设为新的零点（空载时手动去皮）。"""
        with self._st.lock:
            if not self._st.serial_open:
                return {"ok": False, "error": "串口未连接"}
            if self._st.frame_count <= 0:
                return {"ok": False, "error": "尚未收到通道帧"}
            self._st.zero = list(self._st.channels)
            self._st.frame_count = max(self._st.frame_count, 5)
            self._st.left = 0.0
            self._st.right = 0.0
            self._st.last_msg = "已清零：当前空载 = 0N"
            return {"ok": True, "zero": list(self._st.zero)}

    def connect_tcp(self, ip: str):
        try:
            self._tcp.open(ip)
            return {"ok": True}
        except Exception as exc:
            self._msg(f"压力机连接失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def disconnect_tcp(self):
        self._tcp.close(with_shutdown=True)
        self._msg("压力机已断开")
        return {"ok": True}

    def set_side(self, side: int):
        with self._st.lock:
            self._st.active_side = 1 if int(side) == 1 else 5
        return {"ok": True}

    def serial_write_params(self, side, values):
        try:
            side = 1 if int(side) == 1 else 5
            text = " ".join(str(v) for v in values)
            payload = bytes([0xA5, 0x5A, 0x01, side]) + text.encode("latin-1", errors="replace") + bytes([0xB6, 0x0D])
            self._serial.write(payload)
            self._msg(f"{'左' if side == 1 else '右'}侧写参数: {text}")
            return {"ok": True}
        except Exception as exc:
            self._msg(f"写参数失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def serial_command(self, kind: str, arg: str = ""):
        try:
            with self._st.lock:
                side = self._st.active_side
            if kind == "read":
                payload = bytes([0xA5, 0x5A, 0x01, side + 1, 0xB6, 0x0D])
            elif kind == "shutdown":
                payload = bytes([0xA5, 0x5A, 0x01, 0x07, 0xB6, 0x0D])
            elif kind == "name":
                payload = bytes([0xA5, 0x5A, 0x01, 0x09, 0xB6, 0x0D])
            elif kind == "g":
                payload = bytes([0xA5, 0x5A, 0x01, 0x08]) + str(arg).encode("ascii") + bytes([0xB6, 0x0D])
            else:
                return {"ok": False, "error": "未知命令"}
            self._serial.write(payload)
            self._msg(f"串口命令 {kind} {arg}".strip())
            return {"ok": True}
        except Exception as exc:
            self._msg(f"串口命令失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def tcp_command(self, kind: str, arg=None):
        try:
            if kind == "test":
                with self._io_lock:
                    self._tcp.send_hex(TCP_TEST)
                    time.sleep(0.1)
                    self._tcp.send_hex(TCP_TEST)
                self._msg("压力机: 测试")
            elif kind == "stop":
                self._seq_cancel.set()
                with self._io_lock:
                    self._tcp.send_hex(TCP_STOP)
                self._msg("压力机: 停止")
            elif kind == "stop_home":
                self._seq_cancel.set()
                with self._io_lock:
                    self._tcp.send_hex(TCP_STOP_HOME)
                self._msg("压力机: 停止并回位")
            elif kind == "init":
                threading.Thread(target=self._send_sequence, args=(TCP_INIT, "初始化参数", False), daemon=True).start()
            elif kind == "pressure":
                frames = TCP_PRESSURE.get(int(arg))
                if not frames:
                    return {"ok": False, "error": "无此压力点"}
                seq = list(frames)
                self._seq_cancel.clear()
                threading.Thread(target=self._send_sequence, args=(seq, f"{arg}N", True), daemon=True).start()
            else:
                return {"ok": False, "error": "未知命令"}
            return {"ok": True}
        except Exception as exc:
            self._msg(f"压力机命令失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def _send_sequence(self, frames, label, cancellable):
        if not self._seq_lock.acquire(blocking=False):
            self._msg(f"压力机忙，已忽略: {label}")
            return
        try:
            for frame in frames:
                if cancellable and self._seq_cancel.is_set():
                    self._msg(f"压力机: {label} 已中止")
                    return
                with self._io_lock:
                    if cancellable and self._seq_cancel.is_set():
                        self._msg(f"压力机: {label} 已中止")
                        return
                    try:
                        self._tcp.send_hex(frame)
                    except Exception as exc:
                        self._msg(f"压力机发送失败: {exc}")
                        return
                time.sleep(0.1)
            self._msg(f"压力机: {label}")
        finally:
            self._seq_lock.release()

    def start_log(self):
        try:
            path = self._logger.start()
            return {"ok": True, "path": path}
        except Exception as exc:
            self._msg(f"开始导出失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def stop_log(self):
        self._logger.stop()
        self._msg("结束导出")
        return {"ok": True}

    def open_log_folder(self):
        try:
            CSV_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(CSV_DIR))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def pick_files(self):
        if self._window is None:
            return []
        try:
            import webview
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=("CSV 文件 (*.csv)", "所有文件 (*.*)"),
            )
            return list(result) if result else []
        except Exception as exc:
            self._msg(f"选择文件失败: {exc}")
            return []

    def read_text(self, path: str):
        try:
            data = Path(path).read_bytes()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        for enc in ("utf-8-sig", "gbk", "latin-1"):
            try:
                return {"ok": True, "text": data.decode(enc)}
            except UnicodeDecodeError:
                continue
        return {"ok": False, "error": "无法识别文件编码"}

    def shutdown(self):
        try:
            self._logger.stop()
        except Exception:
            pass
        try:
            self._serial.close()
        except Exception:
            pass
        try:
            self._tcp.close(with_shutdown=True)
        except Exception:
            pass

    def on_closing(self):
        self.shutdown()
        return True
