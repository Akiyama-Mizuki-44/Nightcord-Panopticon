"""
macOS 本机一键启动脚本，只用于本地开发/联调，不影响生产部署（生产还是走 deploy.sh + systemd）。
用法：
    python3 run.py            # 正常启动
    python3 run.py --debug    # 打开 Flask 调试模式（FLASK_DEBUG=1）
自动完成：建 .venv（如果没有）、装/更新依赖、config.yaml 缺失时从 config.example.yaml 复制一份占位配置，
然后用 venv 里的解释器启动 app.py，并在默认浏览器里打开面板。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, ".venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python3")
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
CONFIG_EXAMPLE = os.path.join(BASE_DIR, "config.example.yaml")
DEFAULT_PORT = 1810


def ensure_macos():
    if platform.system() != "Darwin":
        sys.exit(
            "run.py 是给 macOS 本机开发用的。其它系统请照 SETUP.md 手动执行："
            "python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py"
        )


def ensure_venv():
    if not os.path.exists(VENV_PYTHON):
        print(f"==> 未找到 {VENV_DIR}，创建虚拟环境...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
    print("==> 安装/更新依赖...")
    subprocess.run(
        [VENV_PYTHON, "-m", "pip", "install", "-q", "-r", os.path.join(BASE_DIR, "requirements.txt")],
        check=True,
    )


def ensure_config():
    if os.path.exists(CONFIG_PATH):
        return
    print("==> 未找到 config.yaml，从 config.example.yaml 复制一份占位配置...")
    shutil.copy(CONFIG_EXAMPLE, CONFIG_PATH)
    print(f"    请编辑 {CONFIG_PATH} 填入真实面板信息，重新运行 run.py 前记得保存。")


def open_browser_later(url):
    time.sleep(1.5)
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="本机启动 Nightcord Panopticon（macOS）")
    parser.add_argument("--debug", action="store_true", help="打开 Flask 调试模式")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"本机监听端口，默认 {DEFAULT_PORT}。如果你有一条 ssh -L 1810:127.0.0.1:1810 "
             "连生产服务器的隧道开着，务必换个端口（比如 --port 18100），不然浏览器打开的很可能"
             "是隧道那头的生产环境，不是这次本机联调的实例。",
    )
    args = parser.parse_args()

    ensure_macos()
    ensure_venv()
    ensure_config()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Thread(target=open_browser_later, args=(url,), daemon=True).start()

    env = os.environ.copy()
    if args.debug:
        env["FLASK_DEBUG"] = "1"
    env["PORT"] = str(args.port)

    print(f"==> 启动 Panopticon（{url}），Ctrl+C 退出")
    os.execve(VENV_PYTHON, [VENV_PYTHON, os.path.join(BASE_DIR, "app.py")], env)


if __name__ == "__main__":
    main()
