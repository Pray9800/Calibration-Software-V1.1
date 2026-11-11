import sys
from pathlib import Path

import webview

from backend import Api


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def main():
    api = Api()
    index = resource_dir() / "web" / "index.html"
    window = webview.create_window(
        "GAPSENSE S2 标定软件",
        url=index.as_uri(),
        js_api=api,
        width=1440,
        height=960,
        min_size=(1180, 760),
    )
    api.bind(window)
    window.events.closing += api.on_closing
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
