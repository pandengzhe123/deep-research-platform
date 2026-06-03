@echo off
chcp 65001 >nul
title 导出研究报告

cd /d "D:\open_deep_research-main\open_deep_research-main"

echo.
echo ============================================
echo   导出 Deep Research 报告
echo ============================================
echo.

set PYTHONUTF8=1

.venv\Scripts\python -c "
import requests, sys, os, json
from datetime import datetime

BASE = 'http://127.0.0.1:2024'

# 1. 列出所有线程
resp = requests.get(f'{BASE}/threads')
threads = resp.json()
if not threads:
    print('没有找到任何研究会话，请先在 Studio 中完成一次研究。')
    sys.exit(1)

# 2. 找到最新的那个
latest = threads[-1]
tid = latest['thread_id']
print(f'找到 {len(threads)} 个会话，最新: {tid[:8]}...')

# 3. 获取状态
resp = requests.get(f'{BASE}/threads/{tid}/state')
state = resp.json()
values = state.get('values', {})

# 4. 提取报告
report = values.get('final_report', '')
messages = values.get('messages', [])
research_brief = values.get('research_brief', '')

if not report:
    print('这个会话还没有生成最终报告，请等待研究完成。')
    sys.exit(1)

# 5. 提取标题
title = '研究报告'
if research_brief:
    title = research_brief.strip().split('\n')[0][:60]

# 6. 保存
safe_title = ''.join(c for c in title if c.isalnum() or c in ' _-').strip()
if len(safe_title) > 50:
    safe_title = safe_title[:50]
date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
filename = f'reports/{date_str}_{safe_title}.md'

os.makedirs('reports', exist_ok=True)
with open(filename, 'w', encoding='utf-8') as f:
    f.write(report)

print(f'报告已导出: {filename}')
print(f'共 {len(report)} 字符')
" 2>&1

echo.
echo ============================================
pause
