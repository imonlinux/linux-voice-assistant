## Install PulseAudio On Your Wyoming Voice Satellite

> Courtesy of https://github.com/FutureProofHomes/wyoming-enhancements/blob/master/snapcast/docs/2_install_pulseaudio.md with now major modifications

## 1. Connect to your Pi over SSH using the username/password you configured during flashing:

```sh
ssh <your_username>@<pi_IP_address>
```


## 2. If already installed Disable/Stop the entire the Linux Voice Assistant service temporarily. If not installed, proceed to step (3.):

```sh
sudo systemctl disable --now linux-voice-assistant.service
```


## 3. Install PulseAudio drivers and necessary utilities:

```sh
sudo apt-get update
sudo apt-get install \
    pulseaudio \
    pulseaudio-utils \
    pamixer
```


## 4. Reboot your Pi:

```sh
sudo reboot -h now
```


## 5. SSH back in to your Pi.  (See step 1.)


## 6. (OPTIONAL) If plugging speakers into the headphone jack of the ReSpeaker, set the correct PulseAudio Sink Port:

Run `pactl list sinks` and scroll to the bottom and notice your Active Port is probably "analog-output-speaker".  Run the command below to output audio through the 3.5mm headphone jack.

```sh
pactl set-sink-port 1 "analog-output-headphones"
```

**Please note that if you are setting this up on a Raspberry Pi 3B or 4B, you need an additional step at this point to make it route the audio through the 2mic hat (and likely the 4mic one as well). Run the following command to do this.**

```sh
pactl set-default-sink alsa_output.platform-soc_sound.stereo-fallback
```


## 7. Test and make sure you can hear the wav file.:

**Note that if you have a Raspbery Pi 3B or 4B, this may or may not seem to work, but it will output on the hat as intended.**

Test mpv can ouput to PA with current configs:

```bash
mpv --ao=pulse --audio-device=default --audio-samplerate=44100 /usr/share/sounds/alsa/Front_Center.wav
```

If volume is low, set it to 100%.

```sh
pactl set-sink-volume alsa_output.platform-soc_sound.stereo-fallback 100%
```


### 8. Done! Return to the tutorial to continue the Linux Voice Assistant install.











