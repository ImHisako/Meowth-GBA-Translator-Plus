"""Configuration form component using CustomTkinter."""

from pathlib import Path
import os
from tkinter import filedialog

import customtkinter as ctk

from ...core import TranslationConfig
from ...credentials import CredentialStore, CredentialsError
from ...translator import PROVIDER_PRESETS

# Language display name -> language code (must match languages.py)
LANGUAGES = {
    "English": "en",
    "Chinese": "zh-Hans",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
}

LANG_NAMES = list(LANGUAGES.keys())


class ConfigForm(ctk.CTkFrame):
    """Configuration form for translation settings."""

    def __init__(self, master):
        """Initialize configuration form."""
        super().__init__(master, corner_radius=10)
        self.credential_store = CredentialStore()
        self._active_provider = "deepseek"

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)

        # --- Row 1: ROM File ---
        ctk.CTkLabel(inner, text="ROM File", font=("", 12, "bold")).pack(anchor="w")
        rom_row = ctk.CTkFrame(inner, fg_color="transparent")
        rom_row.pack(fill="x", pady=(2, 8))
        self.rom_entry = ctk.CTkEntry(
            rom_row, placeholder_text="Select a GBA ROM file...", height=30
        )
        self.rom_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            rom_row, text="Browse", width=80, height=30, command=self._browse_rom
        ).pack(side="right")

        # --- Row 1.5: Output Directory ---
        ctk.CTkLabel(inner, text="Output Directory", font=("", 12, "bold")).pack(anchor="w")
        output_row = ctk.CTkFrame(inner, fg_color="transparent")
        output_row.pack(fill="x", pady=(2, 8))
        self.output_entry = ctk.CTkEntry(
            output_row, placeholder_text="Select output directory...", height=30
        )
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            output_row, text="Browse", width=80, height=30, command=self._browse_output
        ).pack(side="right")

        # --- Row 2: Languages ---
        ctk.CTkLabel(inner, text="Languages", font=("", 12, "bold")).pack(anchor="w")
        lang_row = ctk.CTkFrame(inner, fg_color="transparent")
        lang_row.pack(fill="x", pady=(2, 8))

        src_frame = ctk.CTkFrame(lang_row, fg_color="transparent")
        src_frame.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkLabel(src_frame, text="Source:", font=("", 11)).pack(anchor="w")
        self.source_lang = ctk.CTkComboBox(src_frame, values=LANG_NAMES, state="readonly", height=30)
        self.source_lang.set("English")
        self.source_lang.pack(fill="x", pady=(2, 0))

        tgt_frame = ctk.CTkFrame(lang_row, fg_color="transparent")
        tgt_frame.pack(side="right", fill="x", expand=True, padx=(6, 0))
        ctk.CTkLabel(tgt_frame, text="Target:", font=("", 11)).pack(anchor="w")
        self.target_lang = ctk.CTkComboBox(tgt_frame, values=LANG_NAMES, state="readonly", height=30)
        self.target_lang.set("Chinese")
        self.target_lang.pack(fill="x", pady=(2, 0))

        # --- Row 3: Provider + Model ---
        ctk.CTkLabel(inner, text="Translation API", font=("", 12, "bold")).pack(anchor="w")
        pm_row = ctk.CTkFrame(inner, fg_color="transparent")
        pm_row.pack(fill="x", pady=(2, 4))

        prov_frame = ctk.CTkFrame(pm_row, fg_color="transparent")
        prov_frame.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkLabel(prov_frame, text="Provider:", font=("", 11)).pack(anchor="w")
        self.provider = ctk.CTkComboBox(
            prov_frame, values=list(PROVIDER_PRESETS.keys()),
            state="readonly", height=30, command=self._on_provider_change
        )
        self.provider.set("deepseek")
        self.provider.pack(fill="x", pady=(2, 0))

        model_frame = ctk.CTkFrame(pm_row, fg_color="transparent")
        model_frame.pack(side="right", fill="x", expand=True, padx=(6, 0))
        ctk.CTkLabel(model_frame, text="Model:", font=("", 11)).pack(anchor="w")
        self.model_entry = ctk.CTkEntry(model_frame, height=30)
        self.model_entry.insert(0, PROVIDER_PRESETS["deepseek"][1])
        self.model_entry.pack(fill="x", pady=(2, 0))

        # --- Row 4: API Key ---
        self.api_key_label = ctk.CTkLabel(inner, text="API Key:", font=("", 11))
        self.api_key_label.pack(anchor="w", pady=(4, 0))
        self.api_key_entry = ctk.CTkEntry(
            inner, placeholder_text="sk-xxxxxxxxxxxxxxxxxxxxxxxx", height=30, show="*"
        )
        self.api_key_entry.pack(fill="x", pady=(2, 0))
        key_row = ctk.CTkFrame(inner, fg_color="transparent")
        key_row.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(key_row, text="Salva chiavi", width=100, height=26,
                      command=self.save_api_key).pack(side="left", padx=(0, 8))
        self.key_status = ctk.CTkLabel(key_row, text="", font=("", 11), wraplength=450)
        self.key_status.pack(side="left", fill="x", expand=True)
        self.api_key_entry.bind("<FocusOut>", lambda event: self.save_api_key())
        try:
            saved = self.credential_store.load()
            provider = saved.get("last_provider", "deepseek")
            if provider not in PROVIDER_PRESETS:
                provider = "deepseek"
            self._active_provider = provider
            self.provider.set(provider)
            self.api_key_entry.insert(0, saved["providers"].get(provider, ""))
            self._update_provider_fields(provider)
            self.key_status.configure(text="Salvataggio locale automatico (file non cifrato)")
        except CredentialsError as error:
            self.key_status.configure(text=str(error))

        # --- Advanced (collapsible) ---
        self.advanced_visible = False
        self.advanced_button = ctk.CTkButton(
            inner, text="+ Advanced", command=self._toggle_advanced,
            fg_color="transparent", text_color=("gray40", "gray60"),
            hover_color=("gray85", "gray25"), height=24, font=("", 11),
        )
        self.advanced_button.pack(anchor="w", pady=(6, 0))

        self.advanced_frame = ctk.CTkFrame(inner, fg_color="transparent")
        adv_row = ctk.CTkFrame(self.advanced_frame, fg_color="transparent")
        adv_row.pack(fill="x", pady=(4, 0))

        bf = ctk.CTkFrame(adv_row, fg_color="transparent")
        bf.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkLabel(bf, text="Batch Size:", font=("", 11)).pack(anchor="w")
        self.batch_size = ctk.CTkEntry(bf, height=30)
        self.batch_size.insert(0, "30")
        self.batch_size.pack(fill="x", pady=(2, 0))

        wf = ctk.CTkFrame(adv_row, fg_color="transparent")
        wf.pack(side="right", fill="x", expand=True, padx=(6, 0))
        ctk.CTkLabel(wf, text="Max Workers:", font=("", 11)).pack(anchor="w")
        self.max_workers = ctk.CTkEntry(wf, height=30)
        self.max_workers.insert(0, "10")
        self.max_workers.pack(fill="x", pady=(2, 0))

    def _on_provider_change(self, provider_name: str):
        """Save the outgoing key and load only the selected provider's key."""
        if not self.save_api_key():
            self.provider.set(self._active_provider)
            return
        try:
            key = self.credential_store.get(provider_name)
        except CredentialsError as error:
            self.key_status.configure(text=str(error))
            self.provider.set(self._active_provider)
            return
        self._active_provider = provider_name
        self.api_key_entry.delete(0, "end")
        self.api_key_entry.insert(0, key)
        self._update_provider_fields(provider_name)
        self.save_api_key()

    def save_api_key(self) -> bool:
        """Persist the visible key, or forget it when the field is empty."""
        try:
            self.credential_store.save(self._active_provider, self.api_key_entry.get())
        except CredentialsError as error:
            self.key_status.configure(text=str(error))
            return False
        self.key_status.configure(text="Chiavi salvate in locale" if self.api_key_entry.get().strip()
                                  else "Nessuna chiave salvata per questo servizio")
        return True

    def _update_provider_fields(self, provider_name: str):
        """Update default model when provider changes."""
        preset = PROVIDER_PRESETS.get(provider_name)
        if preset:
            self.model_entry.configure(state="normal")
            self.model_entry.delete(0, "end")
            self.model_entry.insert(0, preset[1])
            if provider_name == "deepl":
                self.model_entry.configure(state="disabled", placeholder_text="Automatic (DeepL)")
                self.api_key_label.configure(text="API Keys (DeepL): separa più chiavi con una virgola")
                self.api_key_entry.configure(placeholder_text="chiave1:fx,chiave2:fx,chiave3:fx")
            else:
                self.model_entry.configure(placeholder_text="")
                self.api_key_label.configure(text="API Key:")
                self.api_key_entry.configure(placeholder_text="sk-xxxxxxxxxxxxxxxxxxxxxxxx")

    def _browse_rom(self):
        """Open file dialog to select ROM."""
        filename = filedialog.askopenfilename(
            title="Select GBA ROM",
            filetypes=[("GBA ROM files", "*.gba"), ("All files", "*.*")],
        )
        if filename:
            self.rom_entry.delete(0, "end")
            self.rom_entry.insert(0, filename)

    def _browse_output(self):
        """Open directory dialog to select output directory."""
        dirname = filedialog.askdirectory(
            title="Select Output Directory",
        )
        if dirname:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, dirname)

    def _toggle_advanced(self):
        """Toggle advanced settings visibility."""
        if self.advanced_visible:
            self.advanced_frame.pack_forget()
            self.advanced_button.configure(text="+ Advanced")
            self.advanced_visible = False
        else:
            self.advanced_frame.pack(fill="x")
            self.advanced_button.configure(text="- Advanced")
            self.advanced_visible = True

    def _lang_name_to_code(self, name: str) -> str:
        return LANGUAGES.get(name, "en")

    def get_config(self) -> TranslationConfig:
        """Get current configuration."""
        provider = self.provider.get()
        preset = PROVIDER_PRESETS.get(provider)
        api_key = self.api_key_entry.get().strip()

        # If user selected output dir, put work dir next to it
        output_dir = Path(self.output_entry.get()) if self.output_entry.get() else None
        work_dir = None
        if output_dir:
            work_dir = output_dir.parent / "work"

        return TranslationConfig(
            source_lang=self._lang_name_to_code(self.source_lang.get()),
            target_lang=self._lang_name_to_code(self.target_lang.get()),
            provider=provider if provider else None,
            model=self.model_entry.get().strip() or (preset[1] if preset else None),
            api_key_env=preset[2] if preset else None,
            api_key=api_key if api_key else None,
            batch_size=int(self.batch_size.get()) if self.batch_size.get().isdigit() else 30,
            max_workers=int(self.max_workers.get()) if self.max_workers.get().isdigit() else 10,
            rom_path=Path(self.rom_entry.get()) if self.rom_entry.get() else None,
            output_dir=output_dir,
            work_dir=work_dir,
        )

    def validate(self) -> tuple[bool, str]:
        """Validate configuration."""
        if not self.rom_entry.get():
            return False, "Please select a ROM file"
        rom_path = Path(self.rom_entry.get())
        if not rom_path.exists():
            return False, f"ROM file not found: {rom_path}"
        preset = PROVIDER_PRESETS.get(self.provider.get())
        api_key = self.api_key_entry.get().strip() or (os.environ.get(preset[2], "") if preset else "")
        if not api_key or (self.provider.get() == "deepl" and not any(key.strip() for key in api_key.split(","))):
            return False, "Please enter your API key"
        for label, entry in (("Batch Size", self.batch_size), ("Max Workers", self.max_workers)):
            if not entry.get().isdigit() or int(entry.get()) < 1:
                return False, f"{label} must be a positive integer"
        return True, ""
