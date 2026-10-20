@app.get("/solve")
def solve():
    question = request.args.get("q", "").strip()

    if not question:
        return jsonify({
            "success": False,
            "error": "沒有收到 q 參數"
        }), 400

    print("\n==============================")
    print("收到新的問題：")
    print(question)
    print("==============================\n")

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
        "received_question": question,
        "question_length": len(question),
        "verification_code": "CEXAM-9527",

        "answer_source": "TEST_FAKE_AI",
        "answer": fake_ai_answer,

        "server_time": datetime.now().isoformat()
    })

    response.headers["Cache-Control"] = "no-store"

    return response