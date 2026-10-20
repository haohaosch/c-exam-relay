from flask import Flask, request, jsonify, Response
from datetime import datetime

app = Flask(__name__)

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

    question = request.args.get("q", "").strip()
    nonce = request.args.get("nonce", "").strip()

    if not question:
        return jsonify({
            "success": False,
            "error": "沒有收到 q 參數",
            "nonce": nonce
        }), 400


    # ==========================================
    # 動態 Fake AI 測試
    # ==========================================

    if "HELLO_TEST" in question:

        test_case = "HELLO"

        answer = """#include <stdio.h>

int main(void)
{
    printf("Hello\\n");
    return 0;
}
"""


    elif "SUM_TEST" in question:

        test_case = "SUM"

        answer = """#include <stdio.h>

int main(void)
{
    int i;
    int sum = 0;

    for (i = 1; i <= 10; i++)
    {
        sum += i;
    }

    printf("%d\\n", sum);

    return 0;
}
"""


    else:

        test_case = "UNKNOWN"

        answer = """No matching test case."""


    print("\n==============================")
    print("收到新的問題")
    print("nonce:", nonce)
    print("test_case:", test_case)
    print("question:", question)
    print("==============================\n")


    response = jsonify({

        "success": True,

        "received_question": question,
        "question_length": len(question),

        "nonce": nonce,

        "test_case": test_case,

        "verification_code": "CEXAM-9527",

        "answer_source": "DYNAMIC_FAKE_AI",

        "answer": answer,

        "server_time": datetime.now().isoformat()
    })


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