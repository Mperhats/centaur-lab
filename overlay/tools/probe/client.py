class ProbeClient:
    def ping(self) -> str:
        return "ok"


def _client() -> ProbeClient:
    return ProbeClient()
