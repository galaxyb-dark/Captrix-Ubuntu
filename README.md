# Captrix

Captrix is a local app that reads the Google Live Caption bubble from Chrome and keeps the words in a window you can select, copy, or save.

After installation, open **Captrix** from the app grid. It does not need a terminal.

## What you need

- Ubuntu, or another Linux desktop that uses the standard app menu
- Google Chrome
- These packages:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-atspi-2.0
```

Pasting into another window needs your user to be allowed to use the mouse and keyboard devices. On Ubuntu that is the `input` group:

```bash
sudo usermod -aG input "$USER"
```

Log out and back in after that command. Caption capture still works before you do this. The paste shortcut needs it.

Turn on Live Caption in Chrome: **Settings → Accessibility → Live Caption**.

When a video or an audio is playing, Chrome shows a caption bubble. Captrix follows the bubble that actually contains words and adds them to one transcript.

## Install

```bash
cd Captrix-Ubuntu
chmod +x install.sh
./install.sh
```

That registers Captrix for the user who ran it. It writes an app-menu entry and a `captrix` command using home folder and the folder where the project lives. Nothing is installed system-wide.

Open **Captrix** from the app grid. If it does not show up, log out and back in.

You can also start it in a terminal with `captrix` after `~/.local/bin` is on your `PATH`. Ubuntu adds that directory at login when the folder exists. A new login picks it up.

Run `./install.sh` again if you move the project folder, or when you copy it to another laptop.

## How to use

- Select the words you want, click the ChatGPT input, then press the shortcut. Captrix pastes that selection and presses Enter. Leave Captrix open. The shortcut starts as **F8**.
- The gear button opens the shortcut panel. Click the shortcut, then press the key you want. That press becomes the shortcut. Esc cancels.
- Select any text and press **Ctrl+C**.
- **Copy** copies the selection, or the whole transcript if nothing is selected.
- **Save** opens a file browser so you can choose a folder and write a `.txt` file. **Ctrl+S** opens the same dialog.
- **Clear** wipes the transcript. The sentence still on the bubble is not pasted back in until the words change.
