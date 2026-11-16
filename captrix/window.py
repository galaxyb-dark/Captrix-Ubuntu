"""Local window that shows captured captions for selection and copy."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from captrix.appearance import load_font, save_font, transcript_css
from captrix.binding import Shortcut, load_shortcut, save_shortcut, uses_left_button
from captrix.chord import HotkeyWatch
from captrix.send import paste_and_enter, remove_send_shortcut
from captrix.stitch import common_prefix_len
from captrix.watcher import CaptionWatcher, Reading, init_atspi

POLL_MS = 200

EMPTY_COPY = {
    "no-browser": (
        "Open Google Chrome",
        "Captrix reads the Live Caption bubble from Chrome on this computer. Open Chrome, then turn on Live Caption under Settings → Accessibility.",
        "web-browser-symbolic",
    ),
    "waiting": (
        "Waiting for captions",
        "When the Google Live Caption bubble pops up over a meeting or a video, the words will collect here. Select the part you want, click the other field, then press the shortcut. The gear button opens the shortcut panel: click it, then press the key you want.\n\nTurn it on in Chrome: Settings → Accessibility → Live Caption. Then play some audio.",
        "media-view-subtitles-symbolic",
    ),
    "notice": (
        "Live Caption is open",
        "Chrome’s caption bubble is on screen, but it is not sending words yet. They will show up here as soon as it does.",
        "dialog-warning-symbolic",
    ),
}


class ShortcutWindow(Adw.Dialog):
    def __init__(self, owner: CaptrixWindow) -> None:
        super().__init__()
        self.owner = owner
        self.set_title("Shortcut")
        self.set_content_width(380)
        self.set_follows_content_size(True)
        self.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

        title = Gtk.Label(label="Shortcut")
        title.add_css_class("title")
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_title_widget(title)
        header.set_show_end_title_buttons(False)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda *_: self.close())
        header.pack_end(close)

        name = Gtk.Label(label="Shortcut", xalign=0)
        name.add_css_class("heading")
        name.set_hexpand(True)
        self.button = Gtk.Button()
        self.button.add_css_class("pill")
        self.button.add_css_class("shortcut-key")
        self.button.set_halign(Gtk.Align.END)
        self.button.set_valign(Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: owner.begin_capture())

        card = Gtk.Box(spacing=12)
        card.add_css_class("shortcut-card")
        card.append(name)
        card.append(self.button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(card)
        self.set_child(content)
        self.connect("closed", self._on_closed)
        self.refresh()

    def refresh(self) -> None:
        self.button.set_label(self.owner.shortcut.label)
        if self.owner._capturing:
            self.button.add_css_class("listening")
        else:
            self.button.remove_css_class("listening")

    def _on_closed(self, *_args) -> None:
        owner = self.owner
        if owner._shortcut_window is self:
            owner._shortcut_window = None
        owner.cancel_capture()


class CaptrixWindow(Adw.ApplicationWindow):
    def __init__(self, watcher: CaptionWatcher, **kwargs):
        super().__init__(**kwargs)
        self.watcher = watcher
        self.shortcut = load_shortcut()
        self.font = load_font()
        self._shortcut_window: ShortcutWindow | None = None
        self._capturing = False
        self.set_title("Captrix")
        self.set_default_size(520, 720)
        self._apply_css()

        self.title = Adw.WindowTitle.new("Captrix", "Looking for Google captions")
        header = Adw.HeaderBar()
        header.set_title_widget(self.title)
        gear = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        gear.add_css_class("flat")
        gear.set_tooltip_text("Change the paste shortcut")
        gear.connect("clicked", lambda *_: self.open_shortcut())
        header.pack_end(gear)

        self.copy_btn = Gtk.Button(label="Copy")
        self.copy_btn.set_tooltip_text(
            "Copy the selection, or the whole transcript if nothing is selected"
        )
        self.copy_btn.connect("clicked", lambda *_: self.copy_text())
        self.clear_btn = Gtk.Button(label="Clear")
        self.clear_btn.set_tooltip_text("Clear the transcript")
        self.clear_btn.connect("clicked", lambda *_: self.clear_text())
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.set_tooltip_text("Save the transcript as a text file")
        self.save_btn.connect("clicked", lambda *_: self.save_text())

        self.status_page = Adw.StatusPage(vexpand=True)
        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(
            buffer=self.buffer,
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=18,
            right_margin=18,
            top_margin=16,
            bottom_margin=16,
            pixels_above_lines=2,
            pixels_below_lines=2,
        )
        self.view.add_css_class("transcript")
        self.view.set_monospace(False)
        self.buffer.connect("mark-set", self._remember_selection)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self._remember_selection)
        self.view.add_controller(focus)
        self._send_text = ""

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_child(self.view)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.status_page, "empty")
        self.stack.add_named(self.scrolled, "text")
        self.stack.set_visible_child_name("empty")

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.stack.set_hexpand(True)
        self.panel.add_css_class("transcript-frame")
        self.panel.append(self.stack)

        bottom = Gtk.Box(spacing=6)
        bottom.set_margin_start(10)
        bottom.set_margin_end(10)
        bottom.set_margin_top(8)
        bottom.set_margin_bottom(8)
        bottom.append(self.copy_btn)
        bottom.append(self.save_btn)
        bottom.append(self.clear_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bottom.append(spacer)
        self.font_btn = Gtk.Button.new_from_icon_name("preferences-desktop-font-symbolic")
        self.font_btn.set_tooltip_text(f"Font: {self.font}")
        self.font_btn.connect("clicked", self._choose_font)
        bottom.append(self.font_btn)
        self.shortcut_btn = Gtk.Button.new_from_icon_name(self._shortcut_icon())
        self.shortcut_btn.set_tooltip_text(f"Shortcut: {self.shortcut.label}")
        self.shortcut_btn.connect("clicked", lambda *_: self.open_shortcut())
        bottom.append(self.shortcut_btn)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.panel)
        toolbar.add_bottom_bar(bottom)

        toast_overlay = Adw.ToastOverlay()
        toast_overlay.set_child(toolbar)
        self.toast_overlay = toast_overlay
        self.set_content(toast_overlay)

        copy_action = Gio.SimpleAction.new("copy-all", None)
        copy_action.connect("activate", lambda *_: self.copy_text())
        self.add_action(copy_action)
        clear_action = Gio.SimpleAction.new("clear", None)
        clear_action.connect("activate", lambda *_: self.clear_text())
        self.add_action(clear_action)
        save_action = Gio.SimpleAction.new("save", None)
        save_action.connect("activate", lambda *_: self.save_text())
        self.add_action(save_action)

        self._shown = ""
        self._save_folder = None
        self._dismiss_menu = False
        self._hotkeys = HotkeyWatch(
            self.shortcut.keys,
            self._on_hotkey,
            self._on_shortcut_captured,
            self._on_capture_cancelled,
        )
        button_error = self._hotkeys.start()
        self.connect("destroy", self._on_destroy)
        self._refresh()
        GLib.timeout_add(POLL_MS, self._tick)
        if button_error:
            GLib.idle_add(self._report_send_error, button_error)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            """
            .transcript-frame {
              margin: 10px 12px 4px 12px;
              border-radius: 16px;
              background-color: @card_bg_color;
              border: 1px solid alpha(@window_fg_color, 0.28);
              box-shadow: inset 0 0 0 1px alpha(@window_fg_color, 0.08);
            }
            .transcript-frame statuspage {
              background: transparent;
            }
            .shortcut-card {
              margin: 2px 16px 16px 16px;
              padding: 12px 12px 12px 16px;
              border-radius: 14px;
              background-color: @card_bg_color;
              border: 1px solid alpha(@window_fg_color, 0.16);
            }
            .shortcut-key {
              min-width: 132px;
              min-height: 36px;
              padding: 4px 16px;
              font-weight: 600;
            }
            .shortcut-key.listening {
              background-color: alpha(@accent_bg_color, 0.18);
              color: @accent_color;
              box-shadow: inset 0 0 0 1px alpha(@accent_bg_color, 0.7);
            }
            """
        )
        display = self.get_display()
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._font_provider = Gtk.CssProvider()
        self._apply_font_css()
        Gtk.StyleContext.add_provider_for_display(
            display,
            self._font_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    def _apply_font_css(self) -> None:
        self._font_provider.load_from_string(transcript_css(self.font))

    def _choose_font(self, *_args) -> None:
        dialog = Gtk.FontDialog()
        dialog.set_title("Transcript font")
        dialog.choose_font(
            self,
            Pango.FontDescription.from_string(self.font),
            None,
            self._on_font_chosen,
        )

    def _on_font_chosen(self, dialog: Gtk.FontDialog, result: Gio.AsyncResult) -> None:
        try:
            description = dialog.choose_font_finish(result)
        except GLib.Error:
            return
        if description is None:
            return
        self.font = description.to_string()
        save_font(self.font)
        self._apply_font_css()
        self.font_btn.set_tooltip_text(f"Font: {self.font}")

    def _shortcut_icon(self) -> str:
        if any(0x110 <= code <= 0x117 for code in self.shortcut.keys):
            return "input-mouse-symbolic"
        return "input-keyboard-symbolic"

    def _tick(self) -> bool:
        try:
            self._refresh()
        except Exception:
            import traceback

            traceback.print_exc()
        return True

    def _refresh(self) -> None:
        reading = self.watcher.poll()
        self._show_transcript(reading.transcript)
        self._update_chrome(reading)
        has_text = bool(reading.transcript)
        self.copy_btn.set_sensitive(has_text)
        self.save_btn.set_sensitive(has_text)
        self.clear_btn.set_sensitive(has_text)
        if has_text:
            self.stack.set_visible_child_name("text")
        else:
            self.stack.set_visible_child_name("empty")

    def _update_chrome(self, reading: Reading) -> None:
        capturing = reading.phase == "capturing"
        words = len(reading.transcript.split()) if reading.transcript else 0
        word_label = "1 word" if words == 1 else f"{words} words"
        if capturing:
            subtitle = f"Capturing · {word_label}"
        elif reading.transcript:
            subtitle = f"Caught · {word_label}"
        elif reading.phase == "no-browser":
            subtitle = "Chrome is not open"
        elif reading.phase == "notice":
            subtitle = "Live Caption needs attention"
        else:
            subtitle = "Waiting for the caption bubble"
        self.title.set_subtitle(subtitle)

        phase = reading.phase if reading.phase in EMPTY_COPY else "waiting"
        title, description, icon = EMPTY_COPY[phase]
        if reading.bubble_open and reading.phase == "waiting":
            title = "Live Caption is open"
            description = (
                "The bubble is up. Words will show up here when Chrome transcribes "
                "a meeting or a video."
            )
            icon = "media-view-subtitles-symbolic"
        elif reading.phase == "notice" and reading.notice:
            title = reading.notice
            description = (
                "That message is coming from Chrome’s caption bubble. "
                "Captrix will copy the words in here as soon as Chrome starts transcribing."
            )
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        self.status_page.set_icon_name(icon)

    def _show_transcript(self, text: str) -> None:
        if text == self._shown:
            return
        at_bottom = self._at_bottom()
        current = self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True
        )
        if current == text:
            self._shown = text
            return
        prefix = common_prefix_len(current, text)
        if prefix == 0 and current and text:
            self.buffer.set_text(text)
        elif prefix == len(current):
            self.buffer.insert(self.buffer.get_end_iter(), text[prefix:])
        else:
            start = self.buffer.get_iter_at_offset(prefix)
            end = self.buffer.get_end_iter()
            self.buffer.delete(start, end)
            if prefix < len(text):
                self.buffer.insert(self.buffer.get_end_iter(), text[prefix:])
        self._shown = text
        if at_bottom:
            GLib.idle_add(self._scroll_to_end)

    def _at_bottom(self) -> bool:
        adjustment = self.scrolled.get_vadjustment()
        if adjustment is None:
            return True
        if adjustment.get_upper() <= adjustment.get_page_size() + 1:
            return True
        return adjustment.get_value() >= adjustment.get_upper() - adjustment.get_page_size() - 32

    def _scroll_to_end(self) -> bool:
        end = self.buffer.get_end_iter()
        mark = self.buffer.create_mark(None, end, False)
        self.view.scroll_mark_onscreen(mark)
        self.buffer.delete_mark(mark)
        return False

    def copy_text(self) -> None:
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, True)
            toast = "Copied selection"
        else:
            text = self.watcher.transcript
            toast = "Copied transcript"
        if not text:
            return
        self.get_clipboard().set(text)
        self.toast_overlay.add_toast(Adw.Toast.new(toast))

    def _remember_selection(self, *_args) -> None:
        bounds = self.buffer.get_selection_bounds() or ()
        if len(bounds) != 2:
            return
        start, end = bounds
        text = self.buffer.get_text(start, end, True).strip()
        if not text or text == self._send_text:
            return
        self._send_text = text
        # Publish while this window is still focused. A later paste can then
        # read it after the click has moved focus to the other field.
        self.get_clipboard().set(text)

    def _on_destroy(self, *_args) -> None:
        self._hotkeys.stop()
        if self._shortcut_window is not None:
            self._shortcut_window.force_close()

    def open_shortcut(self) -> None:
        if self._shortcut_window is None:
            self._shortcut_window = ShortcutWindow(self)
        self._shortcut_window.present(self)
        self.begin_capture()

    def begin_capture(self) -> None:
        self._capturing = True
        self._set_capture_accels(True)
        self._hotkeys.begin_capture()
        self._refresh_shortcut_widgets()

    def cancel_capture(self) -> None:
        self._hotkeys.cancel_capture()
        self._capturing = False
        self._set_capture_accels(False)
        self._refresh_shortcut_widgets()

    def _on_hotkey(self, dismiss_menu: bool) -> bool:
        if not self._capturing:
            self.send_to_focused_field(bool(dismiss_menu))
        return False

    def _on_shortcut_captured(self, keys) -> bool:
        if uses_left_button(keys):
            return False
        self.shortcut = Shortcut(keys)
        save_shortcut(self.shortcut)
        self._hotkeys.set_binding(self.shortcut.keys)
        self._capturing = False
        self._set_capture_accels(False)
        self._refresh_shortcut_widgets()
        self.toast_overlay.add_toast(Adw.Toast.new(f"Shortcut set to {self.shortcut.label}"))
        if self._shortcut_window is not None:
            self._shortcut_window.close()
        return False

    def _on_capture_cancelled(self) -> bool:
        self._capturing = False
        self._set_capture_accels(False)
        self._refresh_shortcut_widgets()
        return False

    def _refresh_shortcut_widgets(self) -> None:
        self.shortcut_btn.set_icon_name(self._shortcut_icon())
        self.shortcut_btn.set_tooltip_text(f"Shortcut: {self.shortcut.label}")
        if self._shortcut_window is not None:
            self._shortcut_window.refresh()

    def _set_capture_accels(self, capturing: bool) -> None:
        app = self.get_application()
        if app is None:
            return
        if capturing:
            app.set_accels_for_action("app.quit", [])
            app.set_accels_for_action("win.copy-all", [])
            app.set_accels_for_action("win.save", [])
            app.set_accels_for_action("win.clear", [])
            return
        app.set_accels_for_action("app.quit", ["<Control>q"])
        app.set_accels_for_action("win.copy-all", ["<Control><Shift>c"])
        app.set_accels_for_action("win.save", ["<Control>s"])
        app.set_accels_for_action("win.clear", ["<Control><Shift>BackSpace"])

    def send_to_focused_field(self, dismiss_menu: bool = False) -> None:
        text = self._selected_text().strip()
        if not text:
            self._report_send_error("Select the caption text first.")
            return
        self._send_text = text
        self._dismiss_menu = bool(dismiss_menu)
        self.get_clipboard().set(text)
        threading.Thread(target=self._paste_when_ready, daemon=True).start()

    def _selected_text(self) -> str:
        bounds = self.buffer.get_selection_bounds() or ()
        if len(bounds) == 2:
            start, end = bounds
            text = self.buffer.get_text(start, end, True)
            if text.strip():
                return text
        return self._send_text

    def _paste_when_ready(self) -> None:
        # Ctrl+V asks Captrix for the clipboard on the main loop, so the
        # key press has to wait on this thread until that loop is free.
        time.sleep(0.32)
        error = paste_and_enter(self._send_text, dismiss_menu=self._dismiss_menu)
        if error:
            GLib.idle_add(self._report_send_error, error)

    def _report_send_error(self, message: str) -> bool:
        self.toast_overlay.add_toast(Adw.Toast.new(message))
        if shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "-a", "Captrix", "Captrix", message],
                check=False,
            )
        return False

    def clear_text(self) -> None:
        self._send_text = ""
        if not self.watcher.transcript:
            return
        self.watcher.clear()
        self._shown = ""
        self.buffer.set_text("")
        self.toast_overlay.add_toast(Adw.Toast.new("Transcript cleared"))

    def save_text(self) -> None:
        text = self.watcher.transcript
        if not text:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Save transcript")
        dialog.set_initial_name(datetime.now().strftime("captions-%Y-%m-%d-%H%M.txt"))
        dialog.set_initial_folder(self._initial_save_folder())
        text_filter = Gtk.FileFilter()
        text_filter.set_name("Text files")
        text_filter.add_suffix("txt")
        text_filter.add_pattern("*.txt")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(text_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(text_filter)
        dialog.save(self, None, self._on_save_finished)

    def _initial_save_folder(self) -> Gio.File:
        if self._save_folder is not None:
            return self._save_folder
        documents = Path.home() / "Documents"
        folder = documents if documents.is_dir() else Path.home()
        return Gio.File.new_for_path(str(folder))

    def _on_save_finished(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            destination = dialog.save_finish(result)
        except GLib.Error as error:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                return
            self.toast_overlay.add_toast(Adw.Toast.new("Could not open that folder"))
            return
        if destination is None:
            return
        basename = destination.get_basename() or "captions.txt"
        if not basename.lower().endswith(".txt"):
            parent = destination.get_parent()
            if parent is not None:
                destination = parent.get_child(f"{basename}.txt")
        text = self.watcher.transcript
        if not text.endswith("\n"):
            text += "\n"
        try:
            destination.replace_contents(
                text.encode("utf-8"),
                None,
                False,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                None,
            )
        except GLib.Error:
            self.toast_overlay.add_toast(Adw.Toast.new("Could not save the file"))
            return
        parent = destination.get_parent()
        if parent is not None:
            self._save_folder = parent
        self.toast_overlay.add_toast(Adw.Toast.new(f"Saved {destination.get_basename()}"))


class CaptrixApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.captrix.Captrix",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.watcher = CaptionWatcher()
        self.send_label = "Left + right click"
        self._window: CaptrixWindow | None = None
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        return self._on_command_line(self, command_line)

    def _on_startup(self, _app) -> None:
        try:
            remove_send_shortcut()
        except Exception:
            import traceback

            traceback.print_exc()
        init_atspi()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("win.copy-all", ["<Control><Shift>c"])
        self.set_accels_for_action("win.save", ["<Control>s"])
        self.set_accels_for_action("win.clear", ["<Control><Shift>BackSpace"])

    def _on_activate(self, _app) -> None:
        window = self.props.active_window
        if not isinstance(window, CaptrixWindow):
            window = CaptrixWindow(self.watcher, application=self)
        # Keep a Python reference. Without it the window can be collected
        # and the process exits while the user still expects it to be open.
        self._window = window
        window.present()

    def _on_command_line(self, _app, command_line: Gio.ApplicationCommandLine) -> int:
        arguments = command_line.get_arguments()
        if "--send" in arguments:
            window = self.props.active_window
            if isinstance(window, CaptrixWindow):
                # The field the user clicked keeps focus. Raising Captrix
                # would paste the selection into the caption panel.
                window.send_to_focused_field()
            else:
                self.activate()
            command_line.set_exit_status(0)
            return 0
        self.activate()
        command_line.set_exit_status(0)
        return 0


def run_window() -> int:
    app = CaptrixApplication()
    return app.run(sys.argv)
