"""ターミナルで会話する。

起動: python -m saburo.cli         （新しい会話）
      python -m saburo.cli --last  （前回の会話の続き）
"""

import sys

from . import agent, config, store


def main():
    conversation_id = None
    if "--last" in sys.argv:
        convs = store.list_conversations()
        if convs:
            conversation_id = convs[0]["id"]
            print(f"続きから: {convs[0]['title']}")

    print(f"サブロー: こんにちは、{config.OWNER_NAME}さん。（終了は Ctrl+D か /exit）")
    while True:
        try:
            text = input("\nあなた: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/memories":
            for m in store.list_memories() or []:
                print(f"  [{m['id']}] {m['content']}")
            continue
        if conversation_id is None:
            conversation_id = store.create_conversation(agent.title_from(text))

        print("\nサブロー: ", end="", flush=True)
        for event in agent.chat(conversation_id, text):
            if event["type"] == "text":
                print(event["text"], end="", flush=True)
            elif event["type"] == "error":
                print(f"\n[!] {event['text']}", end="")
        print()


if __name__ == "__main__":
    main()
