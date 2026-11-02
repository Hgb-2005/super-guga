# -*- coding: utf-8 -*-
"""用 Python urllib 直接完成 GitHub OAuth Device Flow, 绕过 gh CLI 网络问题"""
import urllib.request, urllib.parse, json, ssl, time, sys, os

# gh CLI 的公开 OAuth client_id
CLIENT_ID = "Iv1.b231c327f1eaa229"
SCOPE = "repo,read:user,write:public_key"

ctx = ssl.create_default_context()

def api_post(url, data):
    body = urllib.parse.urlencode(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    })
    r = urllib.request.urlopen(req, context=ctx, timeout=30)
    return json.loads(r.read().decode('utf-8'))

# 步骤 1: 请求设备码
print("请求 GitHub 设备码...", flush=True)
try:
    resp = api_post('https://github.com/login/device/code', {
        'client_id': CLIENT_ID,
        'scope': SCOPE,
    })
except Exception as e:
    print(f"[FAIL] 请求设备码失败: {e}", flush=True)
    sys.exit(1)

device_code = resp.get('device_code', '')
user_code = resp.get('user_code', '')
verification_uri = resp.get('verification_uri', 'https://github.com/login/device')
expires_in = resp.get('expires_in', 900)
interval = resp.get('interval', 5)

print(f"\n{'='*60}")
print(f"  一次性代码: {user_code}")
print(f"  打开网址: {verification_uri}")
print(f"  有效期: {expires_in} 秒")
print(f"{'='*60}\n", flush=True)

# 步骤 2: 轮询等待用户授权
print("等待你在浏览器授权... (每 {} 秒检查一次)".format(interval), flush=True)
deadline = time.time() + expires_in
poll_count = 0
while time.time() < deadline:
    poll_count += 1
    time.sleep(interval)
    try:
        token_resp = api_post('https://github.com/login/oauth/access_token', {
            'client_id': CLIENT_ID,
            'device_code': device_code,
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        })
        if 'access_token' in token_resp:
            token = token_resp['access_token']
            print(f"\n[OK] 授权成功! token 长度: {len(token)}", flush=True)
            # 写入文件
            token_file = os.path.join(os.environ.get('USERPROFILE', '.'), '.gh_token.txt')
            with open(token_file, 'w') as f:
                f.write(token)
            print(f"[OK] token 已保存到 {token_file}", flush=True)
            # 立即用 gh auth login --with-token 配置
            sys.exit(0)
        elif token_resp.get('error') == 'authorization_pending':
            print(f"  [{poll_count}] 等待授权中...", flush=True)
        elif token_resp.get('error') == 'slow_down':
            interval += 5
            print(f"  [{poll_count}] 减慢频率, 等待中...", flush=True)
        elif token_resp.get('error') == 'expired_token':
            print("\n[FAIL] 设备码已过期", flush=True)
            sys.exit(1)
        else:
            err = token_resp.get('error', 'unknown')
            desc = token_resp.get('error_description', '')
            print(f"\n[FAIL] 授权错误: {err} - {desc}", flush=True)
            sys.exit(1)
    except Exception as e:
        print(f"  [{poll_count}] 轮询异常: {e}", flush=True)

print("\n[FAIL] 超时, 设备码已过期", flush=True)
sys.exit(1)
