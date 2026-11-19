# Security

cc-notify-tts runs on your Mac: a hook shim, and a local daemon listening on a Unix socket in its own data directory. Its only network use is downloading the speech model from GitHub during `setup`, and, if you turn wording on, talking to an Ollama server on this machine. Nothing about your sessions leaves the machine.

## Reporting a vulnerability

Please report it privately through GitHub: on the repository's **Security** tab, choose **Report a vulnerability**. Don't open a public issue for it.

Include what you found, how to reproduce it, and the version (`cc-notify-tts --version`, or the commit). You'll get a reply within a week.

## Supported versions

Only the latest release gets fixes.
