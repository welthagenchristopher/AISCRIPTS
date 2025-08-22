from pathlib import Path
import json
import os
import re
import argparse
from dataclasses import dataclass, asdict
from typing import Dict, Any, List
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
import time
from random import randint

load_dotenv()



# Dataclass based on json object

@dataclass
class Signature:
    category: str
    name: str
    visible: bool = False
    declaration: str = ""
    seemore: str = ""
    seealso: str = ""
    deprecated: str = ""
    last_updated: str = ""
    path: str = "" 

    @classmethod
    def from_dict(cls, category: str, name: str, props: Dict[str, Any]):
        # Handle missing fields - defaults
        if not isinstance(props, dict):
            props = {}
        return cls(
            category=category,
            name=name,
            visible=props.get("visible", False),
            declaration=props.get("declaration", ""),
            seemore=props.get("seemore", ""),
            seealso=props.get("seealso", ""),
            deprecated=props.get("deprecated", ""),
            last_updated=props.get("last_updated", ""),
            path=props.get("path", "")
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("category")
        d.pop("name")
        return d



# Loads all Json obejcts into memory for faster access

class JsonSerializer:
    def __init__(self, json_path: Path):
        self.json_path = Path().cwd() / '_Index.json'
        self.signatures: Dict[str, Dict[str, Signature]] = {}
        self._load_all_signatures()

    def _load_all_signatures(self):
        raw = self.json_path.read_text(encoding="utf-8")
        data = json.loads(raw)

        for category, entries in data.items():
            if not isinstance(entries, dict):
                continue

            self.signatures[category] = {}
            for name, props in entries.items():
                if props is None or not isinstance(props, dict):
                    continue
                sig = Signature.from_dict(category, name, props)
                self.signatures[category][name] = sig

    def get_signature(self, category: str, name: str) -> Signature:
        return self.signatures[category][name]

    def update_signature_in_memory(self, updated_sig: Signature):
        cat = updated_sig.category
        name = updated_sig.name
        if cat not in self.signatures or name not in self.signatures[cat]:
            raise KeyError(f"Signature '{name}' not found in category '{cat}'")
        self.signatures[cat][name] = updated_sig

    def save_to_file(self):
        serialized: Dict[str, Dict[str, Any]] = {}
        for category, entries in self.signatures.items():
            serialized[category] = {}
            for name, sig in entries.items():
                serialized[category][name] = sig.to_dict()

        self.json_path.write_text(
            json.dumps(serialized, indent=2),
            encoding="utf-8"
        )



# Sends each signature (object name eg: IntToStr) to ChatGPT, writes doc to sig.path,
# sets sig.seemore to returned link, and sets sig.last_updated to today.

class ApiCaller:
    def __init__(self, serializer: JsonSerializer, system_prompt: str, project_base: Path):
        self.serializer = serializer
        self.system_prompt = system_prompt.strip()
        self.project_base = project_base

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key.startswith("sk-"):
            raise RuntimeError("No valid OpenAI API key in OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key)

    def _build_chat_messages(self, sig: Signature) -> List[dict]:
        """
        Send only the JSON payload under role="user". All formatting instructions live in the system prompt.
        """
        payload = {
            "declaration": sig.declaration,
            "visible": sig.visible,
            "seemore": sig.seemore,
            "seealso": sig.seealso,
            "deprecated": sig.deprecated,
            "last_updated": sig.last_updated,
            "path": sig.path
        }
        user_content = "```json\n" + json.dumps(payload, indent=2) + "\n```"

        messages: List[dict] = []
        if self.system_prompt:
            messages.append({"role": "developer", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

    def _call_chatgpt(self, messages: List[dict]) -> str:
        resp = self.client.chat.completions.create(
            model="o3",
            messages=messages
        )
        return resp.choices[0].message.content.strip()

    def _extract_parts(self, raw_text: str) -> str | str:
        """
        1) Strip any ```json … ``` fences.
        2) Find substring between first '[' and last ']'.
        3) json.loads(...) that substring.
        4) Return (documentation, source).
        """
        fenced_pattern = r"```json\s*(.*?)```"
        match = re.search(fenced_pattern, raw_text, flags=re.DOTALL)
        content = match.group(1).strip() if match else raw_text.strip()

        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1 or end < start:
            raise ValueError(f"Could not locate JSON array in response:\n{raw_text}")

        json_str = content[start : end + 1]

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"ChatGPT did not return valid JSON array:\n{json_str}\nError: {e}")

        if not isinstance(parsed, list) or len(parsed) != 2:
            raise ValueError("Expected a JSON array of exactly two objects.")

        if "documentation" not in parsed[0] or "source" not in parsed[1]:
            raise ValueError("JSON objects must be [{... 'documentation': ...}, {... 'source': ...}].")

        documentation = parsed[0]["documentation"]
        source_url = parsed[1]["source"]

        return documentation, source_url

    def test_update_single(self, category: str, name: str):
        sig = self.serializer.get_signature(category, name)
        print("Before (in-memory):", sig)

        messages = self._build_chat_messages(sig)
        raw = self._call_chatgpt(messages)

        documentation, source_url = self._extract_parts(raw)

        out_path = self.project_base / sig.path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(documentation + "\n", encoding="utf-8")
        print(f"Wrote documentation to {out_path}")

        sig.seemore = source_url
        sig.last_updated = datetime.now().strftime("%Y-%m-%d")
        self.serializer.update_signature_in_memory(sig)

        print("After (in-memory):", sig)

    def run_all_updates(self):
        for category, entries in self.serializer.signatures.items():
            for name, sig in entries.items():
                out_path = self.project_base / sig.path
                if out_path.exists():
                    # fi already exists patch timestamp
                    mtime = out_path.stat().st_mtime
                    sig.last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                    self.serializer.update_signature_in_memory(sig)
                    continue
                print(f"→ Processing {category}/{name} …")
                messages = self._build_chat_messages(sig)
                raw = self._call_chatgpt(messages)
                try:
                    documentation, source_url = self._extract_parts(raw)
                except Exception as e:
                    print(f"   [!] Error parsing response for {category}/{name}: {e}")
                    continue
           
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(documentation + "\n", encoding="utf-8")
                print(f"   Wrote documentation → {out_path}")

                sig.seemore = source_url
                sig.last_updated = datetime.now().strftime("%Y-%m-%d")
                self.serializer.update_signature_in_memory(sig)
                print(f"   Updated in-memory (seemore/last_updated) → {sig}\n")
            
                time.sleep(3)

        self.serializer.save_to_file()
        print("All changes have been saved to index.json.")


# Copy Pasted CLI interface from web - handles tests, runs, and individual tests.

def main():
    sys_prompt_file = Path().cwd() / 'prompt.txt'

    parser = argparse.ArgumentParser(
        description="Generate Delphi documentation via ChatGPT and update index.json."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_parser = subparsers.add_parser("test", help="Test update for a single signature")
    test_parser.add_argument("category", type=str, help="Category (e.g. 'ctFunction')")
    test_parser.add_argument("name", type=str, help="Name (e.g. 'IsSourceInternal')")

    run_parser = subparsers.add_parser("run", help="Run updates for all signatures")

    parser.add_argument(
        "--index-file",
        type=Path,
        default=Path.cwd() / "_Index.json",
        help="Path to index.json (default: ../CodeLibrary/_Index.json)"
    )
    parser.add_argument(
        "--project-base",
        type=Path,
        default=Path.cwd().parent / "CodeLibrary",
        help="Base directory for relative paths (default: ../CodeLibrary)"
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default=sys_prompt_file.read_text(encoding='utf-8'),
        help="System prompt for ChatGPT"
    )

    args = parser.parse_args()

    serializer = JsonSerializer(args.index_file)
    api_caller = ApiCaller(serializer, args.system_prompt, args.project_base)

    if args.command == "test":
        api_caller.test_update_single(args.category, args.name)
    elif args.command == "run":
        api_caller.run_all_updates()


if __name__ == "__main__":
    main()
