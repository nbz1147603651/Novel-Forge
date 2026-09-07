#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Emitter, Manager, PhysicalSize, Size, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

const MAX_PARITY_CAPTURE_DIMENSION: u32 = 7_680;
const DESKTOP_RESUMED_EVENT: &str = "nimo://desktop-resumed";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct EngineRuntimeConfig {
    mode: String,
    base_url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    access_token: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_revision: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    backend_mode: Option<String>,
    read_only: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    startup_diagnostic: Option<String>,
    backend_reload: bool,
}

fn engine_runtime_config() -> Option<EngineRuntimeConfig> {
    let raw_mode = env::var("NIMO_ENGINE_MODE").ok();
    let raw_base_url = env::var("NIMO_ENGINE_BASE_URL").ok();
    if raw_mode.is_none() && raw_base_url.is_none() {
        return None;
    }

    let mode = match raw_mode
        .as_deref()
        .unwrap_or("legacy")
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "mock" => "mock",
        "legacy" | "http" => "legacy",
        _ => return None,
    };
    let base_url = raw_base_url
        .unwrap_or_else(|| "http://127.0.0.1:8000".to_owned())
        .trim()
        .trim_end_matches('/')
        .to_owned();
    let valid_url = (base_url.starts_with("http://") || base_url.starts_with("https://"))
        && base_url.len() <= 2_048
        && !base_url.chars().any(char::is_control);
    if !valid_url {
        return None;
    }

    let access_token = env::var("NIMO_ENGINE_ACCESS_TOKEN")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let expected_revision = env::var("NIMO_ENGINE_EXPECTED_REVISION")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let backend_mode = env::var("NIMO_ENGINE_BACKEND_MODE")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let startup_diagnostic = env::var("NIMO_ENGINE_STARTUP_DIAGNOSTIC")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let read_only = matches!(
        env::var("NIMO_ENGINE_READ_ONLY").as_deref(),
        Ok("1") | Ok("true")
    );
    let backend_reload = matches!(
        env::var("NIMO_ENGINE_BACKEND_RELOAD").as_deref(),
        Ok("1") | Ok("true")
    );
    Some(EngineRuntimeConfig {
        mode: mode.to_owned(),
        base_url,
        access_token,
        expected_revision,
        backend_mode,
        read_only,
        startup_diagnostic,
        backend_reload,
    })
}

fn engine_runtime_initialization_script(config: &EngineRuntimeConfig) -> String {
    let value = serde_json::to_string(config).expect("engine runtime config is serializable");
    format!(
        "Object.defineProperty(window, '__NIMO_ENGINE_CONFIG__', {{ value: Object.freeze({value}), configurable: false }});"
    )
}

/// Return the query string used by the native screenshot runner, if any.
///
/// It is intentionally constrained to a short ASCII query beginning with the
/// explicit fixture switch.  The runner can only navigate the already-loaded
/// local Tauri origin; it cannot turn an environment variable into an external
/// navigation target in a production build.
fn parity_capture_query() -> Option<String> {
    let query = env::var("NIMO_UI_PARITY_QUERY").ok()?;
    let is_safe = query.starts_with("?__nimo_ui_parity=1")
        && query.len() <= 512
        && query.chars().all(|character| character.is_ascii_graphic());
    is_safe.then_some(query)
}

/// Put the restricted capture query on the document before React is allowed
/// to read its persisted UI session.
///
/// Changing ``window.location`` from an ``on_page_load`` callback races with
/// the first React render: the previous route can briefly read a real user's
/// stored session before the replacement navigation wins.  The isolated
/// capture harness does not need a visible URL, only a deterministic fixture.
/// A document-start value gives the frontend the same typed query input before
/// any bundle code runs, while normal application launches receive no value.
fn parity_capture_initialization_script(query: &str) -> String {
    // ``parity_capture_query`` already restricts this to short printable ASCII
    // with a known prefix. Rust's debug representation adds the required
    // JavaScript escaping without turning the environment into executable
    // source code.
    let query_literal = format!("{query:?}");
    format!(
        "Object.defineProperty(window, '__NIMO_UI_PARITY_QUERY__', {{ value: {query_literal}, configurable: false }});"
    )
}

/// Parse the client viewport requested by the macOS screenshot harness.
///
/// This is deliberately unavailable outside the already restricted parity
/// fixture.  Product windows keep the declarative size from `tauri.conf.json`;
/// only the isolated capture process compensates for macOS title-bar chrome so
/// that its WebView content area is exactly the requested fixture viewport.
fn parity_capture_viewport() -> Option<(u32, u32)> {
    parity_capture_query()?;
    let value = env::var("NIMO_UI_PARITY_CAPTURE_VIEWPORT").ok()?;
    parse_parity_capture_viewport(&value)
}

fn parse_parity_capture_viewport(value: &str) -> Option<(u32, u32)> {
    let (width, height) = value.split_once('x')?;
    let width = width.parse::<u32>().ok()?;
    let height = height.parse::<u32>().ok()?;
    ((1..=MAX_PARITY_CAPTURE_DIMENSION).contains(&width)
        && (1..=MAX_PARITY_CAPTURE_DIMENSION).contains(&height))
    .then_some((width, height))
}

/// Give the isolated screenshot fixture an exact title-bar-free client area.
///
/// On macOS a 1440x900 decorated window is clamped to the desktop work area
/// (883 logical pixels on the parity host). Removing decorations for the
/// isolated fixture lets its client rectangle match the PySide6 capture
/// without changing a user-facing application window.
fn apply_parity_capture_viewport(
    window: &tauri::WebviewWindow,
    viewport: (u32, u32),
) -> tauri::Result<()> {
    let scale_factor = window.scale_factor()?;
    let requested_client = PhysicalSize::new(
        (f64::from(viewport.0) * scale_factor).round() as u32,
        (f64::from(viewport.1) * scale_factor).round() as u32,
    );
    window.set_decorations(false)?;
    window.set_size(Size::Physical(requested_client))?;
    eprintln!(
        "NIMO parity capture viewport: requested_client={}x{}, requested_physical={}x{}, scale={scale_factor}, decorations=false",
        viewport.0,
        viewport.1,
        requested_client.width,
        requested_client.height,
    );
    Ok(())
}

fn main() {
    let capture_query = parity_capture_query();
    let capture_viewport = parity_capture_viewport();
    let engine_config = engine_runtime_config();

    let builder = tauri::Builder::default();
    let initialization_script = [
        capture_query
            .as_deref()
            .map(parity_capture_initialization_script),
        engine_config
            .as_ref()
            .map(engine_runtime_initialization_script),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join("\n");
    let builder = if initialization_script.is_empty() {
        builder
    } else {
        builder.append_invoke_initialization_script(initialization_script)
    };
    let app = builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .manage(SleepInhibitorState(Mutex::new(SleepInhibitor::new())))
        .invoke_handler(tauri::generate_handler![
            cmd_select_directory,
            cmd_pick_audio_reference_file,
            cmd_save_file_dialog,
            cmd_reveal_file_in_folder,
            cmd_play_audio,
            cmd_stop_audio,
            cmd_acquire_sleep_inhibitor,
            cmd_release_sleep_inhibitor,
            cmd_register_shortcuts,
            cmd_report_frontend_render_failure,
        ])
        .setup(move |app| {
            if let Some(viewport) = capture_viewport {
                let window = app
                    .get_webview_window("main")
                    .expect("Tauri parity capture requires the main window");
                apply_parity_capture_viewport(&window, viewport)?;
            }
            register_navigation_shortcuts(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building NIMO desktop");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Resumed) {
            // Desktop WebKit can resume without emitting a DOM visibility
            // event.  The React side refreshes its Engine projections from
            // this signal, which also gives the WebView a fresh render pass
            // without reloading unsaved reader drafts.
            let _ = app_handle.emit_to("main", DESKTOP_RESUMED_EVENT, ());
        }
    });
}

// ── Tauri Commands: File System ─────────────────────────────────────────────

/// Open a native directory picker dialog. Returns the selected path or null.
#[tauri::command]
async fn cmd_select_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |path| {
        let _ = tx.send(path.map(|p| p.to_string()));
    });
    // The callback is synchronous in Tauri 2 blocking dialog
    Ok(rx.recv().unwrap_or(None))
}

/// Open a native picker for a voice-clone reference and return its absolute path.
///
/// The local Engine runs on the same host as Tauri, so it needs a real path
/// rather than a browser ``File.name``.  This command is deliberately limited
/// to the formats accepted by the Voice Studio clone workflow; it never reads
/// or uploads the selected bytes itself.
#[tauri::command]
async fn cmd_pick_audio_reference_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .add_filter("Voice reference audio", &["wav", "mp3", "flac", "m4a"])
        .pick_file(move |path| {
            let _ = tx.send(path.map(|value| value.to_string()));
        });
    Ok(rx.recv().unwrap_or(None))
}

/// Open a native save-file dialog. Returns the chosen path or null.
#[tauri::command]
async fn cmd_save_file_dialog(
    app: tauri::AppHandle,
    default_name: String,
    filter_ext: Option<String>,
) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let mut builder = app.dialog().file().set_file_name(&default_name);
    if let Some(ref ext) = filter_ext {
        let name = ext.trim_start_matches('.').to_uppercase();
        builder = builder.add_filter(name, &[ext.trim_start_matches('.')]);
    }
    builder.save_file(move |path| {
        let _ = tx.send(path.map(|p| p.to_string()));
    });
    Ok(rx.recv().unwrap_or(None))
}

// ── Tauri Commands: Audio Playback ──────────────────────────────────────────

/// Reveal an existing file in the platform file manager.
///
/// Canonicalization is deliberate: the webview cannot use this command to
/// pass shell fragments, nonexistent paths, or unresolved relative paths.
#[tauri::command]
async fn cmd_reveal_file_in_folder(path: String) -> Result<(), String> {
    let canonical = canonical_reveal_file(&path)?;
    let mut command = reveal_file_command(&canonical);

    command
        .spawn()
        .map_err(|error| format!("Failed to open file manager: {error}"))?;
    Ok(())
}

fn canonical_reveal_file(path: &str) -> Result<PathBuf, String> {
    let requested = path.trim();
    if requested.is_empty() {
        return Err("Cannot reveal an empty file path".to_owned());
    }
    let canonical = std::fs::canonicalize(requested)
        .map_err(|error| format!("Cannot reveal file {requested}: {error}"))?;
    if !canonical.is_file() {
        return Err(format!(
            "Cannot reveal a non-file target: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

fn reveal_file_command(canonical: &Path) -> Command {
    #[cfg(target_os = "macos")]
    {
        let mut command = Command::new("open");
        command.arg("-R").arg(&canonical);
        command
    }

    #[cfg(target_os = "windows")]
    {
        let mut command = Command::new("explorer");
        command.arg(format!("/select,{}", canonical.display()));
        command
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let target = canonical.parent().unwrap_or(canonical);
        let mut command = Command::new("xdg-open");
        command.arg(target);
        command
    }
}

/// Play an audio file using the system default player (afplay on macOS).
#[tauri::command]
async fn cmd_play_audio(path: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        Command::new("afplay")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to play audio: {e}"))?;
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = path;
    }
    Ok(())
}

/// Stop any playing audio (kills afplay processes).
#[tauri::command]
async fn cmd_stop_audio() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("pkill").arg("afplay").output();
    }
    Ok(())
}

// ── Tauri Commands: Sleep Inhibitor ─────────────────────────────────────────

struct SleepInhibitor {
    #[cfg(target_os = "macos")]
    child: Option<std::process::Child>,
    held: bool,
}

struct SleepInhibitorState(Mutex<SleepInhibitor>);

impl SleepInhibitor {
    fn new() -> Self {
        Self {
            #[cfg(target_os = "macos")]
            child: None,
            held: false,
        }
    }

    fn acquire(&mut self) {
        if self.held {
            return;
        }
        #[cfg(target_os = "macos")]
        {
            if let Ok(child) = Command::new("caffeinate")
                .args(["-i", "-w", &std::process::id().to_string()])
                .spawn()
            {
                self.child = Some(child);
            }
        }
        self.held = true;
    }

    fn release(&mut self) {
        if !self.held {
            return;
        }
        #[cfg(target_os = "macos")]
        {
            if let Some(mut child) = self.child.take() {
                let _ = child.kill();
            }
        }
        self.held = false;
    }
}

/// Prevent the system from sleeping (for long-running tasks).
#[tauri::command]
fn cmd_acquire_sleep_inhibitor(state: State<'_, SleepInhibitorState>) -> Result<(), String> {
    let mut inhibitor = state.0.lock().map_err(|e| e.to_string())?;
    inhibitor.acquire();
    Ok(())
}

/// Allow the system to sleep again.
#[tauri::command]
fn cmd_release_sleep_inhibitor(state: State<'_, SleepInhibitorState>) -> Result<(), String> {
    let mut inhibitor = state.0.lock().map_err(|e| e.to_string())?;
    inhibitor.release();
    Ok(())
}

// ── Tauri Commands: Global Shortcuts ────────────────────────────────────────

#[derive(Clone, Serialize)]
struct ShortcutEvent {
    action: String,
}

/// Register navigation shortcuts (Ctrl+1~4, Ctrl+comma, Ctrl+Alt+N).
fn register_navigation_shortcuts(app: &mut tauri::App) -> tauri::Result<()> {
    let shortcuts: Vec<(Shortcut, &str)> = vec![
        (
            Shortcut::new(Some(Modifiers::CONTROL), Code::Digit1),
            "nav.dashboard",
        ),
        (
            Shortcut::new(Some(Modifiers::CONTROL), Code::Digit2),
            "nav.projects",
        ),
        (
            Shortcut::new(Some(Modifiers::CONTROL), Code::Digit3),
            "nav.workflow",
        ),
        (
            Shortcut::new(Some(Modifiers::CONTROL), Code::Digit4),
            "nav.chapter_studio",
        ),
        (
            Shortcut::new(Some(Modifiers::CONTROL), Code::Comma),
            "nav.settings",
        ),
        (
            Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyN),
            "pet.toggle",
        ),
    ];

    for (shortcut, action) in shortcuts {
        let action_str = action.to_string();
        let _ = app
            .global_shortcut()
            .on_shortcut(shortcut, move |app, _shortcut, _event| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.emit(
                        "nimo://shortcut",
                        ShortcutEvent {
                            action: action_str.clone(),
                        },
                    );
                }
            });
    }
    Ok(())
}

/// Explicitly register shortcuts (called from frontend if needed).
#[tauri::command]
fn cmd_register_shortcuts() -> Result<(), String> {
    // Shortcuts are already registered in setup(); this is a no-op ack.
    Ok(())
}

// ── Tauri Commands: Frontend Diagnostics ───────────────────────────────────

const FRONTEND_FAILURE_SUMMARY_LIMIT: usize = 800;
const FRONTEND_FAILURE_STACK_LIMIT: usize = 2_400;

/// Preserve an actionable WebView render error in the local development log.
///
/// This accepts only bounded, single-line diagnostic fields. It never writes
/// project content, sends data over the network, or changes application state.
#[tauri::command]
fn cmd_report_frontend_render_failure(summary: String, component_stack: String) {
    eprintln!(
        "NIMO frontend render failure | summary={} | component_stack={}",
        compact_frontend_diagnostic(&summary, FRONTEND_FAILURE_SUMMARY_LIMIT),
        compact_frontend_diagnostic(&component_stack, FRONTEND_FAILURE_STACK_LIMIT),
    );
}

fn compact_frontend_diagnostic(value: &str, limit: usize) -> String {
    value
        .chars()
        .filter(|character| !character.is_control())
        .take(limit)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{
        canonical_reveal_file, compact_frontend_diagnostic, engine_runtime_initialization_script,
        parity_capture_initialization_script, parse_parity_capture_viewport, reveal_file_command,
        EngineRuntimeConfig,
    };
    use std::ffi::OsStr;

    #[test]
    fn parses_a_bounded_capture_viewport() {
        assert_eq!(parse_parity_capture_viewport("1440x900"), Some((1440, 900)));
    }

    #[test]
    fn rejects_malformed_or_oversized_capture_viewports() {
        assert_eq!(parse_parity_capture_viewport("1440×900"), None);
        assert_eq!(parse_parity_capture_viewport("0x900"), None);
        assert_eq!(parse_parity_capture_viewport("7681x900"), None);
    }

    #[test]
    fn injects_the_capture_query_before_document_scripts_run() {
        let script = parity_capture_initialization_script(
            "?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember",
        );

        assert!(script.contains("__NIMO_UI_PARITY_QUERY__"));
        assert!(script.contains("page=dashboard"));
        assert!(script.contains("configurable: false"));
    }

    #[test]
    fn serializes_runtime_engine_config_as_data() {
        let script = engine_runtime_initialization_script(&EngineRuntimeConfig {
            mode: "legacy".to_owned(),
            base_url: "https://engine.example.test".to_owned(),
            access_token: Some("quote'\"token".to_owned()),
            expected_revision: None,
            backend_mode: Some("external".to_owned()),
            read_only: false,
            startup_diagnostic: None,
            backend_reload: false,
        });

        assert!(script.contains("__NIMO_ENGINE_CONFIG__"));
        assert!(script.contains("https://engine.example.test"));
        assert!(script.contains(r#"quote'\"token"#));
        assert!(script.contains("Object.freeze"));
    }

    #[test]
    fn compacts_frontend_failure_diagnostics_before_logging() {
        assert_eq!(compact_frontend_diagnostic("a\n\u{0000}b", 10), "ab");
        assert_eq!(compact_frontend_diagnostic("abcdef", 3), "abc");
    }

    #[test]
    fn reveal_file_uses_the_canonical_file_and_platform_selection_command() {
        let path = std::env::temp_dir().join(format!(
            "nimo-reveal-test-{}-{}.json",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system time after epoch")
                .as_nanos(),
        ));
        std::fs::write(&path, "{}\n").expect("create temporary reveal fixture");
        let canonical = canonical_reveal_file(path.to_str().expect("utf-8 temp path"))
            .expect("canonical temporary file");
        let command = reveal_file_command(&canonical);

        #[cfg(target_os = "macos")]
        {
            assert_eq!(command.get_program(), OsStr::new("open"));
            assert_eq!(
                command.get_args().collect::<Vec<_>>(),
                vec![OsStr::new("-R"), canonical.as_os_str()]
            );
        }
        #[cfg(target_os = "windows")]
        {
            assert_eq!(command.get_program(), OsStr::new("explorer"));
            assert_eq!(
                command.get_args().collect::<Vec<_>>(),
                vec![OsStr::new(&format!("/select,{}", canonical.display()))]
            );
        }
        #[cfg(all(unix, not(target_os = "macos")))]
        {
            assert_eq!(command.get_program(), OsStr::new("xdg-open"));
            assert_eq!(
                command.get_args().collect::<Vec<_>>(),
                vec![canonical
                    .parent()
                    .expect("temporary file parent")
                    .as_os_str()]
            );
        }
        std::fs::remove_file(path).expect("remove temporary reveal fixture");
    }

    #[test]
    fn reveal_file_rejects_nonexistent_paths_and_directories() {
        let missing = std::env::temp_dir().join("nimo-reveal-file-does-not-exist");
        assert!(canonical_reveal_file(missing.to_str().expect("utf-8 temp path")).is_err());
        assert!(
            canonical_reveal_file(std::env::temp_dir().to_str().expect("utf-8 temp dir")).is_err()
        );
    }
}
