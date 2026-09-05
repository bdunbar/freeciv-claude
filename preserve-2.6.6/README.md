# freeciv 2.6.6 debs, preserved before the Pop!_OS 24.04 upgrade

Ubuntu 24.04 (noble) ships freeciv **3.1.0** and has **no gtk2 client**.
fcbot speaks the 2.6 wire protocol and hardcodes the `freeciv-2.6.6`
capability string, so 3.1.0 will not work with it.

These are the jammy binaries currently installed. They depend on jammy
libraries and will NOT simply install on noble -- they are kept as a
reference/fallback source, not a drop-in.

Post-upgrade options, best first:
1. Run freeciv-server 2.6.6 in a Docker container (docker is already
   installed). fcbot talks TCP to the server, so this is a clean seam.
2. Build freeciv 2.6.6 from source on noble.
3. Regenerate fcbot/protocol/ from freeciv 3.1's packets.def -- a real
   port, not a config change.
