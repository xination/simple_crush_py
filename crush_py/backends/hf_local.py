from .base import BackendError, BaseBackend


class HuggingFaceLocalBackend(BaseBackend):
    name = "hf_local"

    def __init__(self, *args, **kwargs):
        raise BackendError("HF local backend is not implemented yet.")

    def generate(self, system_prompt, messages, tools=None):
        raise BackendError("HF local backend is not implemented yet.")

    def stream_generate(self, system_prompt, messages, tools=None):
        raise BackendError("HF local backend is not implemented yet.")
