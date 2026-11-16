"""Local window that shows captured captions for selection and copy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

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
        "When the Google Live Caption bubble pops up over a meeting or a video, the words will collect here. You can select them and copy.\n\nTurn it on in Chrome: Settings → Accessibility → Live Caption. Then play some audio.",
        "media-view-subtitles-symbolic",
    ),
    "notice": (
        "Live Caption is open",
        "Chrome’s caption bubble is on screen, but it is not sending words yet. They will show up here as soon as it does.",
        "dialog-warning-symbolic",
    ),
}


class CaptrixWindow(Adw.ApplicationWindow):
    def __init__(self, watcher: CaptionWatcher, **kwargs):
        super().__init__(**kwargs)
        self.watcher = watcher
        self.set_title("Captrix")
        self.set_default_size(520, 720)
        self._apply_css()

        self.title = Adw.WindowTitle.new("Captrix", "Looking for Google captions")
        header = Adw.HeaderBar()
        header.set_title_widget(self.title)

        self.live_dot = Gtk.Label(label="●")
        self.live_dot.add_css_class("live-dot")
        self.live_dot.add_css_class("idle")
        self.live_dot.set_tooltip_text("Not receiving captions")
        header.pack_start(self.live_dot)

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

        self.scrolled = Gtk.ScrolledWindow(vexpand=True)
        self.scrolled.add_css_class("card")
        self.scrolled.set_margin_start(12)
        self.scrolled.set_margin_end(12)
        self.scrolled.set_margin_top(6)
        self.scrolled.set_margin_bottom(6)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled.set_child(self.view)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.status_page, "empty")
        self.stack.add_named(self.scrolled, "text")
        self.stack.set_visible_child_name("empty")

        self.hint = Gtk.Label(xalign=0, hexpand=True)
        self.hint.add_css_class("dim-label")
        self.hint.set_ellipsize(Pango.EllipsizeMode.END)
        self.hint.set_text("Waiting for captions")

        bottom = Gtk.Box(spacing=6)
        bottom.set_margin_start(10)
        bottom.set_margin_end(10)
        bottom.set_margin_top(8)
        bottom.set_margin_bottom(8)
        bottom.append(self.copy_btn)
        bottom.append(self.save_btn)
        bottom.append(self.clear_btn)
        bottom.append(self.hint)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
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
        self._refresh()
        GLib.timeout_add(POLL_MS, self._tick)

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            """
            textview.transcript,
            textview.transcript text {
              background: transparent;
              font-size: 16pt;
            }
            .live-dot {
              color: #2ec27e;
              font-size: 10pt;
              margin-left: 6px;
            }
            .live-dot.idle {
              color: alpha(@window_fg_color, 0.35);
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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
        if capturing:
            self.live_dot.remove_css_class("idle")
            self.live_dot.set_tooltip_text("Receiving captions")
        else:
            self.live_dot.add_css_class("idle")
            self.live_dot.set_tooltip_text("Not receiving captions")

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

        if reading.transcript or capturing:
            hint = "Select text, then Ctrl+C"
        elif reading.notice:
            hint = reading.notice
        elif reading.phase == "no-browser":
            hint = "Open Google Chrome to start catching captions."
        else:
            hint = "Play a meeting or a video with Live Caption turned on."
        self.hint.set_text(hint)

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

    def clear_text(self) -> None:
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
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.watcher = CaptionWatcher()
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)

    def _on_startup(self, _app) -> None:
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
        if window is None:
            window = CaptrixWindow(self.watcher, application=self)
        window.present()


def run_window() -> int:
    app = CaptrixApplication()
    return app.run(None)
