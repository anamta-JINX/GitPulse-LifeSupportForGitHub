# Contributing to GitPulse

Thanks for your interest in GitPulse.

Before opening a pull request, please open an issue or contact the maintainer so
larger changes can be discussed first. Keep changes focused, preserve the Git
safety model, and include tests for behavioral changes.

## Development checks

```powershell
py -3 -m unittest discover -s tests -v
```

For Windows packaging changes, also build and verify the standalone app:

```powershell
scripts\windows\build_exe.bat
```

## Ownership and licensing

GitPulse remains copyright © 2026 Anamta Gohar. Submitting a contribution does
not transfer ownership of the GitPulse project. See `LICENSE` for repository
terms.

Contact: anamta.gohar25@gmail.com
