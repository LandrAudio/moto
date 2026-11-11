# LANDR Moto fork

Branch `landr/5.2.3`: Moto's 5.2.3 tag plus one commit per fix LANDR's emulated test kit
(LandrAudio/LANDR.BuildTools, `emulation/`) needs, each with its regression test. Version
`5.2.3+landr.N`. Image: `Dockerfile.landr` (same recipe and packages as `motoserver/moto:5.2.3`).

Upstream status per fix: the kit's `emulation/kit/stack/moto/PATCHES.md`. When a fix lands
upstream, the next fork rebases onto the Moto release that has it and drops the commit.
