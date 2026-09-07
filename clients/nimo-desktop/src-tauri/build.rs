use std::{fs, path::Path};

fn rerun_if_frontend_asset_changes(path: &Path) {
    println!("cargo:rerun-if-changed={}", path.display());
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        rerun_if_frontend_asset_changes(&entry.path());
    }
}

fn main() {
    // `tauri build` runs Vite before Cargo, but Cargo otherwise only watches
    // tauri.conf.json.  Track every generated asset so a changed hashed bundle
    // is embedded on the next native build instead of leaving a blank WebView
    // that requests an obsolete JS filename.
    rerun_if_frontend_asset_changes(Path::new("../dist"));
    tauri_build::build()
}
