import asyncio
import re

from dotenv import load_dotenv
import httpx
import os
from textual.app import App, ComposeResult
from textual.message import Message
from textual.screen import Screen
from textual.widgets import (Header, Input, Label, RichLog, Rule, Static,
                             TextArea)

load_dotenv()

# Read in prompt.txt
system_prompt = ""
with open("prompt.txt", "r") as f:
    system_prompt = f.read().strip()

# Read in instructions.txt
instructions = ""
with open("instructions.txt", "r") as f:
    instructions = f.read()

class AccountingLineItem():
    def __init__(self, payee: str, amount: int, is_debit: bool):
        self.payee = payee
        self.amount = amount
        self.is_debit = is_debit
    
    def __str__(self):
        return f"{'Dr' if self.is_debit else 'Cr'}. {self.payee}.... ${self.amount / 100:.2f}"

class WelcomeScreen(Screen):
    CSS = """
    #welcome {
        text-style: bold;
        margin-bottom: 1;
    }

    #startup_name_prompt {
        margin-bottom: 1;
    }

    #startup_name_input {
        max-width: 50;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header("FrickBooks")
        yield Label("Welcome to FrickBooks!", id="welcome")
        yield Label("What's the name of your startup?", id="startup_name_prompt")
        yield Input(placeholder="Startup Name", id="startup_name_input")
    
    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "startup_name_input":
            self.post_message(self.Chosen(event.input.value))
    
    class Chosen(Message):
        def __init__(self, startup_name):
            super().__init__()
            self.startup_name = startup_name

class ExtendedTextArea(TextArea):
    def edit(self, edit):
        super().edit(edit)
        self.post_message(self.Changed(self.text))

    class Changed(Message):
        def __init__(self, value):
            super().__init__()
            self.value = value

class HomeScreen(Screen):
    CSS = """
    #prompt {
        padding: 0 1 1 1;
    }

    #textarea {
        height: 7;
        margin: 0 1 1 1;
    }
    """
    STANDARD_PROMPT = "Enter in an accounting entry. Press ENTER twice when done."

    def __init__(self, startup_name: str):
        super().__init__()
        self.message_cache = []
        self.startup_name = startup_name
    
    def compose(self) -> ComposeResult:
        yield Header("FrickBooks")
        yield RichLog(id="log", wrap=True, markup=True)
        yield Rule(line_style="thick")
        yield Static(self.STANDARD_PROMPT, id="prompt")
        yield ExtendedTextArea(id="textarea")
    
    def on_mount(self):
        self.query_one("#log").write(f"[#999999]{instructions}")
        self.query_one("#textarea").focus()
    
    async def on_extended_text_area_changed(self, event):
        prompt_label = self.query_one("#prompt")
        text = event.value
        textarea = self.query_one("#textarea")
        if text.endswith("\n\n"):
            textarea.load_text(text[:-2])
            entries = []
            try:
                for line in text.split("\n"):
                    trimmed_line = line.strip()
                    if trimmed_line == "":
                        continue
                    # Case insensitive regex that matches "Dr.John Smith$100" or "Cr. John Smith $200"
                    match = re.match(r"(?i)(Dr|Cr)\.?\s*(.*)\s*\$([0-9]*\.?[0-9]{0,2})", trimmed_line)
                    if match is None:
                        raise Exception("Invalid input.")
                    is_debit = match.group(1).lower() == "dr"
                    payee = match.group(2)
                    amount = round(float(match.group(3)) * 100)
                    entries.append(AccountingLineItem(payee, amount, is_debit))
                if len(entries) == 0:
                    raise Exception("Invalid input.")
            except BaseException as e:
                prompt_label.update("Invalid input. Try again.")
                prompt_label.styles.color = "red"
                return
            
            # Sum all the entries
            total = 0
            for entry in entries:
                if entry.amount < 0:
                    prompt_label.update("Amounts must be positive. Try again.")
                    prompt_label.styles.color = "red"
                    return
                total += entry.amount if entry.is_debit else -entry.amount
            if total != 0:
                prompt_label.update("Debits and credits must balance. Try again.")
                prompt_label.styles.color = "red"
                return

            prompt_label.update("Thinking...")
            prompt_label.styles.color = "blue"
            textarea.disabled = True
            asyncio.create_task(self.submit_entry(entries))
    
    async def submit_entry(self, items: list[AccountingLineItem]):
        log: RichLog = self.query_one("#log")
        prompt_label = self.query_one("#prompt")
        textarea = self.query_one("#textarea")
        try:
            system_message = [{ "role": "system", "content": system_prompt.replace("{NAME}", self.startup_name) }]
            user_message = [{ "role": "user", "content": "\n".join(str(item) for item in items) }]
            request_data = { "model": os.getenv("OPENAI_MODEL"), "messages": system_message + self.message_cache + user_message }
            async with httpx.AsyncClient() as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", json=request_data, headers={
                    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"
                }, timeout=httpx.Timeout(20))
                response_json = response.json()
                response_message = response_json["choices"][0]["message"]
                self.message_cache.append(user_message[0])
                self.message_cache.append(response_message)
                self.message_cache = self.message_cache[-8:]
                content = response_message["content"].strip()
                for item in items:
                    log.write(f"[{'green' if item.is_debit else 'red'}]{item}")
                log.write("\n")
                log.write(response_message["content"])
                log.write("\n")
                textarea.clear()
                if content.endswith("The End."):
                    prompt_label.update(f"{self.startup_name} has shut down.")
                    prompt_label.styles.color = "orange"
                    textarea.disabled = True
                    return
            prompt_label.update(self.STANDARD_PROMPT)
            prompt_label.styles.color = "white"
        except Exception as e:
            prompt_label.update(f"Something went wrong: {e!r}")
            prompt_label.styles.color = "red"
        textarea.disabled = False
        textarea.focus()

class Frickbooks(App):
    CSS = """
    WelcomeScreen {
        align: center middle;
        height: 100%;
    }
    """

    def on_mount(self):
        self.push_screen(WelcomeScreen())
    
    def on_welcome_screen_chosen(self, event: WelcomeScreen.Chosen):
        self.pop_screen()
        self.push_screen(HomeScreen(event.startup_name))
    
if __name__ == "__main__":
    app = Frickbooks()
    app.run()