import os
from typing import List, Dict, Generator
from dotenv import load_dotenv
from dataclasses import asdict, dataclass

# Load environment variables from .env file
load_dotenv()


def get_anthropic(model: str,
                  prompt: str,
                  temperature: float = 0,
                  max_tokens: int = 1024,
                  system: str = '',
                  chat_history: List[Dict] = None,
                  verbose=False) -> \
        Generator[dict, None, None]:
    # https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
    import anthropic

    clawd_key = os.getenv('ANTHROPIC_API_KEY')
    clawd_client = anthropic.Anthropic(api_key=clawd_key) if clawd_key else None

    if chat_history is None:
        chat_history = []

    messages = []

    # Add conversation history, removing cache_control from all but the last two user messages
    for i, message in enumerate(chat_history):
        if message["role"] == "user":
            if i >= len(chat_history) - 3:  # Last two user messages
                messages.append(message)
            else:
                messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": message["content"][0]["text"]}]
                })
        else:
            messages.append(message)

    # Add the new user message
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]
    })

    response = clawd_client.beta.prompt_caching.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=messages,
        stream=True
    )

    output_tokens = 0
    input_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    for chunk in response:
        if chunk.type == "content_block_start":
            # This is where we might find usage info in the future
            pass
        elif chunk.type == "content_block_delta":
            yield dict(text=chunk.delta.text)
        elif chunk.type == "message_delta":
            output_tokens = dict(chunk.usage).get('output_tokens', 0)
        elif chunk.type == "message_start":
            usage = chunk.message.usage
            input_tokens = dict(usage).get('input_tokens', 0)
            cache_creation_input_tokens = dict(usage).get('cache_creation_input_tokens', 0)
            cache_read_input_tokens = dict(usage).get('cache_read_input_tokens', 0)
        else:
            if verbose:
                print("Unknown chunk type:", chunk.type)
                print("Chunk:", chunk)

    if verbose:
        # After streaming is complete, print the usage information
        print(f"Output tokens: {output_tokens}")
        print(f"Input tokens: {input_tokens}")
        print(f"Cache creation input tokens: {cache_creation_input_tokens}")
        print(f"Cache read input tokens: {cache_read_input_tokens}")
    yield dict(output_tokens=output_tokens, input_tokens=input_tokens,
               cache_creation_input_tokens=cache_creation_input_tokens,
               cache_read_input_tokens=cache_read_input_tokens)


def get_openai(model: str,
               prompt: str,
               temperature: float = 0,
               max_tokens: int = 1024,
               system: str = '',
               chat_history: List[Dict] = None,
               verbose=False) -> Generator[dict, None, None]:
    from openai import OpenAI

    openai_key = os.getenv('OPENAI_API_KEY')
    openai_client = OpenAI(api_key=openai_key) if openai_key else None

    if chat_history is None:
        chat_history = []

    messages = [{"role": "system", "content": system}] + chat_history + [{"role": "user", "content": prompt}]

    response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True
    )

    output_tokens = 0
    input_tokens = 0
    for chunk in response:
        if chunk.choices[0].delta.content:
            yield dict(text=chunk.choices[0].delta.content)
        if chunk.usage:
            output_tokens = chunk.usage.completion_tokens
            input_tokens = chunk.usage.prompt_tokens

    if verbose:
        print(f"Output tokens: {output_tokens}")
        print(f"Input tokens: {input_tokens}")
    yield dict(output_tokens=output_tokens, input_tokens=input_tokens)


def openai_messages_to_gemini_history(messages):
    """Converts OpenAI messages to Gemini history format.

    Args:
        messages: A list of OpenAI messages, each with "role" and "content" keys.

    Returns:
        A list of dictionaries representing the chat history for Gemini.
    """
    history = []
    for message in messages:
        if isinstance(message["content"], list):
            message["content"] = message["content"][0]["text"]
        if message["role"] == "user":
            history.append({"role": "user", "parts": [{"text": message["content"]}]})
        elif message["role"] == "assistant":
            history.append({"role": "model", "parts": [{"text": message["content"]}]})
        # Optionally handle system messages if needed
        # elif message["role"] == "system":
        #     history.append({"role": "system", "parts": [{"text": message["content"]}]})

    return history


def get_google(model: str,
               prompt: str,
               temperature: float = 0,
               max_tokens: int = 1024,
               system: str = '',
               chat_history: List[Dict] = None,
               verbose=False) -> Generator[dict, None, None]:
    import google.generativeai as genai

    gemini_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=gemini_key)
    # Create the model
    generation_config = {
        "temperature": temperature,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": max_tokens,
        "response_mime_type": "text/plain",
    }

    if chat_history is None:
        chat_history = []

    chat_history = chat_history.copy()
    chat_history = openai_messages_to_gemini_history(chat_history)

    # NOTE: assume want own control.  Too many false positives by Google.
    from google.generativeai.types import HarmCategory
    from google.generativeai.types import HarmBlockThreshold
    safety_settings = {
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    }
    gemini_model = genai.GenerativeModel(model_name=model,
                                         generation_config=generation_config,
                                         safety_settings=safety_settings,
                                         system_instruction=system,
                                         )
    chat = gemini_model.start_chat(history=chat_history)
    response = chat.send_message(prompt, stream=True)

    output_tokens = 0
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    for chunk in response:
        if chunk.text:
            yield dict(text=chunk.text)
        if chunk.usage_metadata:
            output_tokens = chunk.usage_metadata.candidates_token_count
            input_tokens = chunk.usage_metadata.prompt_token_count
            cache_read_input_tokens = chunk.usage_metadata.cached_content_token_count
            cache_creation_input_tokens = 0

    if verbose:
        print(f"Output tokens: {output_tokens}")
        print(f"Input tokens: {input_tokens}")
    yield dict(output_tokens=output_tokens, input_tokens=input_tokens,
               cache_read_input_tokens=cache_read_input_tokens,
               cache_creation_input_tokens=cache_creation_input_tokens)


def get_groq(model: str,
             prompt: str,
             temperature: float = 0,
             max_tokens: int = 1024,
             system: str = '',
             chat_history: List[Dict] = None,
             verbose=False) -> Generator[dict, None, None]:
    from groq import Groq

    groq_key = os.getenv("GROQ_API_KEY")
    client = Groq(api_key=groq_key)

    if chat_history is None:
        chat_history = []

    chat_history = chat_history.copy()

    messages = [{"role": "system", "content": system}] + chat_history + [{"role": "user", "content": prompt}]

    stream = client.chat.completions.create(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )

    output_tokens = 0
    input_tokens = 0
    for chunk in stream:
        if chunk.choices[0].delta.content:
            yield dict(text=chunk.choices[0].delta.content)
        if chunk.usage:
            output_tokens = chunk.usage.completion_tokens
            input_tokens = chunk.usage.prompt_tokens

    if verbose:
        print(f"Output tokens: {output_tokens}")
        print(f"Input tokens: {input_tokens}")
    yield dict(output_tokens=output_tokens, input_tokens=input_tokens)


def get_openai_azure(model: str,
                     prompt: str,
                     temperature: float = 0,
                     max_tokens: int = 1024,
                     system: str = '',
                     chat_history: List[Dict] = None,
                     verbose=False) -> Generator[dict, None, None]:
    from openai import AzureOpenAI

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")  # e.g. https://project.openai.azure.com
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")  # i.e. deployment name with some models deployed
    azure_api_version = os.getenv('OPENAI_API_VERSION', '2024-07-01-preview')
    assert azure_endpoint is not None, "Azure OpenAI endpoint not set"
    assert azure_key is not None, "Azure OpenAI API key not set"
    assert azure_deployment is not None, "Azure OpenAI deployment not set"

    client = AzureOpenAI(
        azure_endpoint=azure_endpoint,
        api_key=azure_key,
        api_version=azure_api_version,
        azure_deployment=azure_deployment,
    )

    if chat_history is None:
        chat_history = []

    messages = [{"role": "system", "content": system}] + chat_history + [{"role": "user", "content": prompt}]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True
    )

    output_tokens = 0
    input_tokens = 0
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield dict(text=chunk.choices[0].delta.content)
        if chunk.usage:
            output_tokens = chunk.usage.completion_tokens
            input_tokens = chunk.usage.prompt_tokens

    if verbose:
        print(f"Output tokens: {output_tokens}")
        print(f"Input tokens: {input_tokens}")
    yield dict(output_tokens=output_tokens, input_tokens=input_tokens)


def get_model_names():
    anthropic_models = ['claude-3-opus-20240229', 'claude-3-5-sonnet-20240620', 'claude-3-haiku-20240307']
    openai_models = ['gpt-4o', 'gpt-4-turbo-2024-04-09', 'gpt-4o-mini']
    google_models = ['gemini-1.5-pro-latest', 'gemini-1.5-flash-latest']
    groq_models = ['llama3-groq-70b-8192-tool-use-preview', 'llama3-groq-8b-8192-tool-use-preview',
                   'llama-3.1-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768']
    ezure_models = ['azure:' + x for x in openai_models]

    return anthropic_models, openai_models, google_models, groq_models, ezure_models


def get_all_model_names():
    anthropic_models, openai_models, google_models, groq_models, ezure_models = get_model_names()
    return anthropic_models + openai_models + google_models + groq_models + ezure_models


def get_model_api(model: str):
    anthropic_models, openai_models, google_models, groq_models, ezure_models = get_model_names()

    if model in anthropic_models:
        return get_anthropic
    elif model in openai_models:
        return get_openai
    elif model in google_models:
        return get_google
    elif model in groq_models:
        return get_groq
    elif model in ezure_models:
        return get_openai_azure
    else:
        raise ValueError(f"Unsupported model: {model}")
