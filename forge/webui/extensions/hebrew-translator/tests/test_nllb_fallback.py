
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, pipeline

def hebrew_to_english(text):
    """
    Translates Hebrew text to English using the NLLB model.
    """
    model_name = "facebook/nllb-200-distilled-600M"
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        translator = pipeline(
            "translation",
            model=model,
            tokenizer=tokenizer,
            src_lang="heb_Hebr",
            tgt_lang="eng_Latn",
            max_length=512,
        )
        result = translator(text)
        translated_text = result[0]["translation_text"].strip()
        if translated_text.startswith('-'):
            translated_text = translated_text[1:].strip()
        return translated_text
    except Exception as e:
        return f"Translation failed: {e}"

if __name__ == "__main__":
    test_text = "שלום עולם"
    translated_text = hebrew_to_english(test_text)
    print(f"Original text: {test_text}")
    print(f"Translated text: {translated_text}")
    assert translated_text.lower().strip() == "hello world"
