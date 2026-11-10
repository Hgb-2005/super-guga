"""Owned, bounded browser fixture for a genuine mouse-selection acceptance."""
import http.server,time,webbrowser

PAGE='''<!doctype html><html><head><meta charset="utf-8"><title>Guga Selection Acceptance</title>
<style>body{font:24px Arial,"Microsoft YaHei";padding:90px;background:#f8faff;color:#263557}#sample{font-size:32px;padding:30px;background:white;border:1px solid #ccd6ed}#sitebar{display:none;position:fixed;z-index:2147483647;background:#f1b447;padding:12px;font-size:18px;border-radius:8px}</style></head>
<body><h1>Guga Selection Acceptance</h1><p>本页仅用于咕嘎左键划词验收，不包含用户资料。</p>
<div id="sample">Guga selection works here. 左键选中文字，显示截图提问和解释。</div><div id="sitebar">网页自己的划词浮层</div>
<script>document.addEventListener('mouseup',e=>{let b=document.getElementById('sitebar');if(String(getSelection()).trim()){b.style.left=(e.clientX+8)+'px';b.style.top=Math.max(10,e.clientY-50)+'px';b.style.display='block'}else b.style.display='none'})</script></body></html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.end_headers();self.wfile.write(PAGE.encode('utf-8'))
    def log_message(self,*args):pass

if __name__=='__main__':
    server=http.server.HTTPServer(('127.0.0.1',19281),Handler);server.timeout=1
    webbrowser.open('http://127.0.0.1:19281/')
    deadline=time.monotonic()+90
    try:
        while time.monotonic()<deadline:server.handle_request()
    finally:server.server_close()
