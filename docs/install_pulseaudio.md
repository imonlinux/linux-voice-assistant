## Install PulseAudio On Your Wyoming Voice Satellite

> Courtesy of https://github.com/FutureProofHomes/wyoming-enhancements/blob/master/snapcast/docs/2_install_pulseaudio.md with minor modifications

## 1. Connect to your Pi over SSH using the username/password you configured during flashing:

```sh
ssh <your_username>@<pi_IP_address>
```


## 2. If already installed Disable/Stop the entire the Linux Voice Assistant service temporarily. If not installed, proceed to setp 3.:

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


## 6. Ensure PulseAudio is runnining in "system-wide mode".  Per the [official instructions](https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/SystemWide/) we first need to stop some existing services to be safe:

```sh
sudo systemctl --global disable pulseaudio.service pulseaudio.socket
```


## 7. It is also advisable to set `autospawn = no` in `/etc/pulse/client.conf`:

```sh
sudo nano /etc/pulse/client.conf
```


## 8. Create the `PulseAudio.service`:

```sh
sudo cp ./service/pulseaudio.service /etc/systemd/system/pulseaudio.service
```


## 9. Start the `PulseAudio.service`.  This will require you to type in your password a couple times.

```sh
systemctl --system enable pulseaudio.service
```

```sh
systemctl --system start pulseaudio.service
```


## 10. Ensure pi user is in the `pulse-access` group:

```sh
sudo sed -i '/^pulse-access:/ s/$/root,pi/' /etc/group
```


## 11. Reboot your Pi: (see step 4)


## 12. (OPTIONAL) If plugging speakers into the headphone jack, set the correct PulseAudio Sync Port:

Run `pactl list sinks` and scroll to the bottom and notice your Active Port is probably "analog-output-speaker".  Run the command below to output audio through the 3.5mm headphone jack.

```sh
pactl set-sink-port 1 "analog-output-headphones"
```

**Please note that if you are setting this up on a Raspberry Pi 3B or 4B, you need an additional step at this point to make it route the audio through the 2mic hat (and likely the 4mic one as well). Run the following command to do this.**

```sh
pactl set-default-sink alsa_output.platform-soc_sound.stereo-fallback
```


## 13. Test and make sure you can hear the wav file:

```sh
paplay /usr/share/sounds/alsa/Front_Center.wav
```

**Note that if you have a Raspbery Pi 3B or 4B, this may or may not seem to work, but it will output on the hat as intended.**


## 14. Modify PulseAudio to duck the music volume when you or the voice assistant are speaking:

```sh
sudo sed -i.bak \
    -e '$a### Enable Volume Ducking' \
    -e '$aload-module module-role-ducking trigger_roles=announce,phone,notification,event ducking_roles=any_role volume=25%' \
    /etc/pulse/system.pa
```


## 15. Reboot your Pi: (see step 4)


## 16. Done! Return to the tutorial to continue the Linux Voice Assistant install.
