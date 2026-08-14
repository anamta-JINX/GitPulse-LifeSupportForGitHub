# GitPulse copyright and brand notice

GitPulse source code and documentation are copyright © 2026 **Anamta Gohar**
(`<anamta.gohar25@gmail.com>`).

The source code is available under the [MIT License](LICENSE). The copyright
and permission notice in `LICENSE` must remain in every copy or substantial
portion of the software, including modified or redistributed versions.

The **GitPulse** name, product identity, logo, icon, banner and other branded
visual assets remain the original creative work of Anamta Gohar. The MIT grant
for the software does not grant permission to imply endorsement by Anamta
Gohar, misrepresent the original authorship, or use the brand identity as the
identity of an unrelated product.

## Third-party component

The lightweight root `GitPulse.exe` uses the 64-bit Windows GUI launcher from
`distlib` 0.4.0. Its license is preserved in
[`THIRD_PARTY_LICENSES/distlib-LICENSE.txt`](THIRD_PARTY_LICENSES/distlib-LICENSE.txt).
The launcher stub retained at `tools/w64-launcher.exe` carries the GitPulse
application icon. The project-local build script appends GitPulse's
`__main__.py` payload to produce the functional root executable.
