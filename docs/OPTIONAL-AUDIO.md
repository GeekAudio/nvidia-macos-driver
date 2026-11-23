# Optional HD Audio (Tahoe)

The complete driver package carries NMXAudio 1.0.2 under `Optional/`. The
installer asks separately and defaults to **No**. Skip it if your audio already
works. This component supports PCI HD Audio controllers on macOS 26.

HDMI and DisplayPort playback devices appear when the connected monitor
reports stereo audio support. macOS can adjust output volume, mute and balance
for those devices. Onboard labels display jack roles, locations and colors from
the codec's active configuration when the pin group reports them consistently.
If AppleHDAController matches the onboard controller, the system uses AppleHDA
for those ports and can still use NMXAudio for NVIDIA display audio. NMXAudio
can provide onboard audio when AppleHDAController is unavailable.

For noninteractive installs, use `sudo bash install.sh --audio=yes` to opt in,
or `--audio=no` to remove audio previously installed by this fork. Updates retain
that opt-in. The independent Intel-panel prompt is unchanged.

An independently installed or subsequently replaced NMXAudio is preserved.
Remove it through its own installer before selecting the bundled copy. Audio
installed by this fork participates in kernel preflight, backup, interrupted
installation recovery, boot fallback and uninstall.

On macOS 26.7.4 with AppleALC 1.9.9 and SimpleHDA 1.0.1, AppleHDA took over
the onboard ALC897. The same boot verified T2751U DisplayPort playback on
the RTX 3080, along with system volume and stereo balance. An earlier diagnostic
build also verified mute and ALC897 labels. Virtual-display attachment did not
create an audio endpoint. Physical HDMI/DisplayPort hot-plug remains unverified.

The package includes the Liberator and FreeBSD HD Audio licenses under
`licenses/`.
