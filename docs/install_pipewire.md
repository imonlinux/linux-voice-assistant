> 
> Install Pipewire On Your Linux Voice Satellite
> 


## 1. Connect to your Pi over SSH using the username/password you configured during flashing:

```sh
ssh <your_username>@<pi_IP_address>
```


## 2. If already installed Disable/Stop the entire the Linux Voice Assistant service. If not installed, proceed to step (3.):

```sh
systemctl --user stop linux-voice-assistant.service
systemctl --user disable --now linux-voice-assistant.service
```


## 3. Install Pipewire packages and necessary PulseAudio utilities:

```sh
sudo apt-get update
sudo apt-get install \
    pulseaudio-utils \
    pipewire wireplumber \
    pipewire-audio libspa-0.2-modules
```


## 4. Reboot your Pi:

```sh
sudo reboot -h now
```


## 5. SSH back in to your Pi.  (See step 1.)


## 6. Ensure Pipewire is runnining in user mode. 

```sh
systemctl --user status pipewire.service
systemctl --user status pipewire-pulse.service

```


## 7. (OPTIONAL) If plugging speakers into the headphone jack of the ReSpeaker, set the correct PulseAudio Sink Port:

Run `pactl list sinks` and scroll to the bottom and notice your Active Port is probably "analog-output-speaker".  Run the command below to output audio through the 3.5mm headphone jack.

```sh
pactl set-sink-port alsa_output.platform-soc_sound.stereo-fallback "analog-output-headphones"
```

**Please note that if you are setting this up on a Raspberry Pi 3B or 4B, you need an additional step at this point to make it route the audio through the 2mic hat (and likely the 4mic one as well). Run the following command to do this.**

```sh
pactl set-default-sink alsa_output.platform-soc_sound.stereo-fallback
```

**Note that if you have a Raspbery Pi 3B or 4B, this may or may not seem to work, but it will output on the hat as intended.**


## 8. Test and make sure you can hear the wav file.:

```sh
mpv --ao=pipewire --audio-device=default --audio-samplerate=44100 /usr/share/sounds/alsa/Front_Center.wav
```

If volume is low, set it to 100%.

```sh
pactl set-sink-volume alsa_output.platform-soc_sound.stereo-fallback 100%
```


## 9. Done! Return to the tutorial to continue the Linux Voice Assistant install.








