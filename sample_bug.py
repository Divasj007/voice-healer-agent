"""Small deliberately broken program for the self-healing demo."""


def build_greeting(name: str) -> str:
    greeting = "Hello, " + name
    version = 42
    return greeting + " | demo value: " + version


if __name__ == "__main__":
    print(build_greeting("Hacktoberfest"))
