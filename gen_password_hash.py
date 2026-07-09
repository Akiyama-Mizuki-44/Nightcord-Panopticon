"""
生成 Dashboard 登录密码的哈希，填到 config.yaml 的 dashboard_auth.password_hash 里。
用法：
    python gen_password_hash.py "你的密码"
"""
import sys
from werkzeug.security import generate_password_hash

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python gen_password_hash.py \"你的密码\"")
        sys.exit(1)
    print(generate_password_hash(sys.argv[1]))
