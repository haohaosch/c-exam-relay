from flask import Flask, request, jsonify, Response
from datetime import datetime

app = Flask(__name__)

# JSON 顯示中文，不轉成 \uXXXX
app.json.ensure_ascii = False


@app.get("/")
def home():
    return """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>C Exam Relay</title>
    </head>
    <body>
        <h1>C Exam Relay API</h1>
        <p>Server is running.</p>
    </body>
    </html>
    """


@app.get("/robots.txt")
def robots():
    content = """
User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /
"""
    return Response(content, mimetype="text/plain")


@app.get("/solve")
def solve():
    # 取得題目
    question = request.args.get("q", "").strip()

    # 取得 nonce，用來避免 ChatGPT 讀到舊快取
    nonce = request.args.get("nonce", "").strip()

    if not question:
        return jsonify({
            "success": False,
            "error": "沒有收到 q 參數"
        }), 400

    print("\n==============================")
    print("收到新的問題")
    print("nonce:", nonce)
    print("question:", question)
    print("==============================\n")

    # 目前先用假的 AI 答案測試
    fake_ai_answer = """#include <stdio.h>

int main(void)
{
    int i;

    for (i = 1; i <= 10; i++)
    {
        printf("%d\\n", i);
    }

    return 0;
}
"""

    response = jsonify({
        "success": True,

        # 收到的原始題目
        "received_question": question,
        "question_length": len(question),

        # 每次請求的識別碼 / cache busting
        "nonce": nonce,

        # 測試驗證
        "verification_code": "CEXAM-9527",

        # 目前是假 AI
        "answer_source": "TEST_FAKE_AI",

        # C 程式回答
        "answer": fake_ai_answer,

        # Server 收到請求的時間
        "server_time": datetime.now().isoformat()
    })

    # 儘量避免一般 HTTP cache
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000
    )