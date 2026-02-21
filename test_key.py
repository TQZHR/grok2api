import requests
import json
import uuid

# ================= 配置区 =================
# 1. 在管理后台 [Key 管理] 页面创建一个新 Key
# 2. 将新生成的 sk-... 填入下方
API_KEY = "YOUR_NEW_API_KEY" 
BASE_URL = "http://127.0.0.1:8000"
# ==========================================

def test_chat_completion():
    print(f"开始测试 Key: {API_KEY[:10]}...")
    
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "grok-4.20-beta",
        "messages": [
            {"role": "user", "content": "Hello, who are you? Tell me a joke."}
        ],
        "stream": False
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"状态码: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            print("--- 响应成功 ---")
            print(content)
            print("---------------")
            print("测试通过！现在去管理后台 [日志审计] 确认日志中是否记录了该请求。")
        else:
            print(f"请求失败: {response.text}")
            
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    if API_KEY == "YOUR_NEW_API_KEY":
        print("请先将代码中的 API_KEY 替换为你刚才生成的 Key！")
    else:
        test_chat_completion()
