QOLDA RECOVERY INTELLIGENCE

1. Put these files in the same folder as all QOLDA game HTML files.
2. Rename .env.example to .env.
3. Paste your OpenAI API key into OPENAI_API_KEY.
4. Double-click START_QOLDA_AI.bat.
5. Open http://127.0.0.1:8000/index.html
6. Keep the existing glove bridge running separately on port 8765.

The OpenAI key stays on the Python server and is never placed in HTML.
Voice: click microphone to start, click again to transcribe and send.
The default text model is gpt-5.4-mini; change QOLDA_TEXT_MODEL=gpt-5.5 for maximum reasoning quality.
