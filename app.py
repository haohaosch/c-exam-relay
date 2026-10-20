import os
import secrets
from datetime import datetime

from flask import Flask, request, jsonify, Response
from openai import OpenAI


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)

# JSON 顯示中文
app.json.ensure_ascii = False


# =========================================================
# Environment Variables
# =========================================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-6-luna"
)

OPENAI_REASONING_EFFORT = os.environ.get(
    "OPENAI_REASONING_EFFORT",
    "low"
)

RELAY_TOKEN = os.environ.get(
    "RELAY_TOKEN",
    ""
)

try:
    OPENAI_MAX_OUTPUT_TOKENS = int(
        os.environ.get(
            "OPENAI_MAX_OUTPUT_TOKENS",
            "1800"
        )
    )
except ValueError:
    OPENAI_MAX_OUTPUT_TOKENS = 1800


# =========================================================
# OpenAI Client
# =========================================================

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# =========================================================
# Helper Functions
# =========================================================

def no_cache(response):
    """
    避免一般瀏覽器 / Proxy 快取。
    ChatGPT Search 仍建議每次使用不同 nonce。
    """

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


def check_token():
    """
    PoC 用簡單 Relay Token。

    如果 Render 有設定 RELAY_TOKEN，
    request 就必須帶正確 token。

    如果沒有設定 RELAY_TOKEN，
    則暫時不啟用 token 驗證。
    """

    if not RELAY_TOKEN:
        return True

    provided_token = request.args.get(
        "token",
        ""
    ).strip()

    return secrets.compare_digest(
        provided_token,
        RELAY_TOKEN
    )


def unauthorized_response():
    response = jsonify({
        "success": False,
        "error": "Unauthorized"
    })

    return no_cache(response), 401


# =========================================================
# Home
# =========================================================

@app.get("/")
def home():

    return """
    <!DOCTYPE html>

    <html>

    <head>
        <meta charset="UTF-8">
        <title>C Exam Relay</title>
    </head>

    <body>

        <h1>C Exam Relay API</h1>

        <p>Server is running.</p>

        <p>Architecture:</p>

        <pre>
/submit
   ↓
OpenAI Background Response
   ↓
response_id
   ↓
/result
   ↓
final answer
        </pre>

    </body>

    </html>
    """


# =========================================================
# robots.txt
# =========================================================

@app.get("/robots.txt")
def robots():

    content = """
User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /
"""

    return Response(
        content,
        mimetype="text/plain"
    )


# =========================================================
# Health Check
# =========================================================

@app.get("/health")
def health():

    response = jsonify({

        "success": True,

        "service": "C Exam Relay",

        "backend": "OpenAI Responses API",

        "mode": "background",

        "model": OPENAI_MODEL,

        "reasoning_effort": OPENAI_REASONING_EFFORT,

        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,

        "server_time": datetime.now().isoformat()
    })

    return no_cache(response)


# =========================================================
# SUBMIT
# =========================================================

@app.get("/submit")
def submit():

    # -----------------------------------------------------
    # Token Check
    # -----------------------------------------------------

    if not check_token():
        return unauthorized_response()


    # -----------------------------------------------------
    # Parameters
    # -----------------------------------------------------

    question = request.args.get(
        "q",
        ""
    ).strip()

    nonce = request.args.get(
        "nonce",
        ""
    ).strip()


    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if not question:

        response = jsonify({
            "success": False,
            "error": "沒有收到 q 參數",
            "nonce": nonce
        })

        return no_cache(response), 400


    # 防止有人丟超長 prompt 燒 API
    if len(question) > 5000:

        response = jsonify({
            "success": False,
            "error": "題目過長",
            "max_length": 5000,
            "question_length": len(question),
            "nonce": nonce
        })

        return no_cache(response), 400


    if not OPENAI_API_KEY:

        response = jsonify({
            "success": False,
            "error": "Server 尚未設定 OPENAI_API_KEY",
            "nonce": nonce
        })

        return no_cache(response), 500


    # -----------------------------------------------------
    # Log
    # -----------------------------------------------------

    print("\n========================================")
    print("NEW BACKGROUND REQUEST")
    print("nonce:", nonce)
    print("model:", OPENAI_MODEL)
    print("reasoning:", OPENAI_REASONING_EFFORT)
    print("question:")
    print(question)
    print("========================================\n")


    # -----------------------------------------------------
    # OpenAI Background Request
    # -----------------------------------------------------

    try:

        ai_response = client.responses.create(

            model=OPENAI_MODEL,

            reasoning={
                "effort": OPENAI_REASONING_EFFORT
            },

            instructions="""
你是一個專門處理 C 語言程式設計考題的程式設計助教。

請仔細分析使用者提供的題目後作答。

要求：

1. 必須使用 C 語言。

2. 先正確理解：
   - 輸入格式
   - 輸出格式
   - 題目限制
   - 邊界條件

3. 如果題目需要演算法，
   請選擇正確且合理有效率的方法。

4. 特別注意：
   - 陣列邊界
   - 整數溢位
   - 迴圈條件
   - 字串結尾
   - 指標
   - 記憶體
   - scanf / printf 格式
   - 空格
   - 換行
   - 大小寫
   - 特殊符號

5. 如果題目要求輸出特定圖形，
   必須嚴格遵守其空格與換行格式。

6. 不要假設題目沒有提供的條件。

7. 最後必須提供完整、
   可以直接編譯執行的 C 程式碼。

8. 不要加入與解題無關的長篇內容。

9. 如果題目資訊不足，
   請明確指出缺少什麼資訊。
""",

            input=question,

            max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,

            # 關鍵：
            # 不等待整個模型推理完成
            background=True
        )


        # -------------------------------------------------
        # Immediately return response_id
        # -------------------------------------------------

        response = jsonify({

            "success": True,

            "stage": "submitted",

            "status": ai_response.status,

            "response_id": ai_response.id,

            "nonce": nonce,

            "received_question": question,

            "question_length": len(question),

            "answer_source": "OPENAI_BACKGROUND_API",

            "model": OPENAI_MODEL,

            "reasoning_effort": OPENAI_REASONING_EFFORT,

            "verification_code": "CEXAM-9527",

            "server_time": datetime.now().isoformat()
        })


        return no_cache(response)


    except Exception as e:

        print(
            "OpenAI SUBMIT ERROR:",
            type(e).__name__,
            str(e)
        )

        response = jsonify({

            "success": False,

            "stage": "submit",

            "nonce": nonce,

            "error": "OpenAI background request failed",

            "error_type": type(e).__name__,

            "server_time": datetime.now().isoformat()
        })

        return no_cache(response), 500


# =========================================================
# RESULT
# =========================================================

@app.get("/result")
def result():

    # -----------------------------------------------------
    # Token Check
    # -----------------------------------------------------

    if not check_token():
        return unauthorized_response()


    # -----------------------------------------------------
    # Parameters
    # -----------------------------------------------------

    response_id = (
        request.args.get("id", "").strip()
        or
        request.args.get("response_id", "").strip()
    )

    nonce = request.args.get(
        "nonce",
        ""
    ).strip()


    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if not response_id:

        response = jsonify({

            "success": False,

            "error": "沒有收到 response id",

            "nonce": nonce
        })

        return no_cache(response), 400


    # 基本格式限制
    if not response_id.startswith("resp_"):

        response = jsonify({

            "success": False,

            "error": "response id 格式不正確",

            "response_id": response_id,

            "nonce": nonce
        })

        return no_cache(response), 400


    # -----------------------------------------------------
    # Retrieve Response
    # -----------------------------------------------------

    try:

        ai_response = client.responses.retrieve(
            response_id
        )

        status = ai_response.status


        print("\n========================================")
        print("RESULT CHECK")
        print("response_id:", response_id)
        print("nonce:", nonce)
        print("status:", status)
        print("========================================\n")


        # -------------------------------------------------
        # Still processing
        # -------------------------------------------------

        if status in {
            "queued",
            "in_progress"
        }:

            response = jsonify({

                "success": True,

                "stage": "processing",

                "status": status,

                "response_id": response_id,

                "nonce": nonce,

                "ready": False,

                "message": "答案仍在產生中，請稍後使用新的 nonce 再查詢。",

                "server_time": datetime.now().isoformat()
            })

            return no_cache(response)


        # -------------------------------------------------
        # Completed
        # -------------------------------------------------

        if status == "completed":

            answer = ai_response.output_text

            response = jsonify({

                "success": True,

                "stage": "completed",

                "status": status,

                "ready": True,

                "response_id": response_id,

                "nonce": nonce,

                "verification_code": "CEXAM-9527",

                "answer_source": "OPENAI_BACKGROUND_API",

                "model": ai_response.model,

                "answer": answer,

                "server_time": datetime.now().isoformat()
            })

            return no_cache(response)


        # -------------------------------------------------
        # Incomplete
        # -------------------------------------------------

        if status == "incomplete":

            response = jsonify({

                "success": False,

                "stage": "incomplete",

                "status": status,

                "ready": False,

                "response_id": response_id,

                "nonce": nonce,

                "error": "OpenAI response incomplete",

                "server_time": datetime.now().isoformat()
            })

            return no_cache(response), 500


        # -------------------------------------------------
        # Failed
        # -------------------------------------------------

        if status == "failed":

            response = jsonify({

                "success": False,

                "stage": "failed",

                "status": status,

                "ready": False,

                "response_id": response_id,

                "nonce": nonce,

                "error": "OpenAI response failed",

                "server_time": datetime.now().isoformat()
            })

            return no_cache(response), 500


        # -------------------------------------------------
        # Cancelled
        # -------------------------------------------------

        if status == "cancelled":

            response = jsonify({

                "success": False,

                "stage": "cancelled",

                "status": status,

                "ready": False,

                "response_id": response_id,

                "nonce": nonce,

                "error": "OpenAI response cancelled",

                "server_time": datetime.now().isoformat()
            })

            return no_cache(response), 500


        # -------------------------------------------------
        # Unknown Status
        # -------------------------------------------------

        response = jsonify({

            "success": False,

            "status": status,

            "ready": False,

            "response_id": response_id,

            "nonce": nonce,

            "error": "未知的 OpenAI response status",

            "server_time": datetime.now().isoformat()
        })

        return no_cache(response), 500


    except Exception as e:

        print(
            "OpenAI RESULT ERROR:",
            type(e).__name__,
            str(e)
        )

        response = jsonify({

            "success": False,

            "stage": "result",

            "response_id": response_id,

            "nonce": nonce,

            "error": "讀取 OpenAI response 失敗",

            "error_type": type(e).__name__,

            "server_time": datetime.now().isoformat()
        })

        return no_cache(response), 500


# =========================================================
# Old /solve Endpoint
# =========================================================

@app.get("/solve")
def old_solve():

    response = jsonify({

        "success": False,

        "deprecated": True,

        "message": "同步 /solve 已停用，請使用 /submit 與 /result。",

        "submit_endpoint": "/submit",

        "result_endpoint": "/result"
    })

    return no_cache(response), 410


# =========================================================
# Local Development
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )