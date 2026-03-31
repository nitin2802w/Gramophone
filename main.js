const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');
const net  = require('net');
const fs   = require('fs');

// ─── Config ───────────────────────────────────────────────────────────────────
const PORT      = 5001;
const DEV       = !app.isPackaged;
const FLASK_URL = `http://127.0.0.1:${PORT}`;

let win        = null;
let flaskProc  = null;
let stderrBuf  = '';   // accumulate Flask stderr for error reporting

Menu.setApplicationMenu(null);

// ─── Find Python ──────────────────────────────────────────────────────────────
function findPython() {
  const candidates = [];

  // Bundled python in packaged builds
  if (!DEV) {
    candidates.push(
      path.join(process.resourcesPath, 'python', 'python.exe'),
      path.join(process.resourcesPath, 'python', 'python3'),
      path.join(process.resourcesPath, 'python', 'python'),
    );
  }

  // Standard PATH names
  candidates.push('python3', 'python', 'py');

  // Windows common locations
  if (process.platform === 'win32') {
    const lad = process.env.LOCALAPPDATA || '';
    const pf  = process.env.PROGRAMFILES || 'C:\\Program Files';
    for (const ver of ['313', '312', '311', '310', '39']) {
      candidates.push(
        path.join(lad, 'Programs', 'Python', `Python${ver}`, 'python.exe'),
        path.join(pf, `Python${ver}`, 'python.exe'),
        `C:\\Python${ver}\\python.exe`,
      );
    }
  }

  // macOS common locations
  if (process.platform === 'darwin') {
    candidates.push(
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      '/usr/bin/python3',
    );
  }

  for (const cmd of candidates) {
    try {
      const out = execSync(`"${cmd}" --version 2>&1`, { encoding: 'utf8', timeout: 3000 });
      if (out.includes('Python 3')) {
        console.log(`[Electron] Found Python: ${cmd} → ${out.trim()}`);
        return cmd;
      }
    } catch { /* try next */ }
  }
  return null;
}

// ─── Port check ───────────────────────────────────────────────────────────────
function isPortFree(port) {
  return new Promise(resolve => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => { srv.close(); resolve(true); });
    srv.listen(port, '127.0.0.1');
  });
}

// ─── Read error log written by app.py ────────────────────────────────────────
function readErrorLog() {
  const logPath = DEV
    ? path.join(__dirname, 'gramophone_error.log')
    : path.join(process.resourcesPath, 'backend', 'gramophone_error.log');
  try {
    if (fs.existsSync(logPath)) return fs.readFileSync(logPath, 'utf8');
  } catch { /* ignore */ }
  return null;
}

// ─── Show detailed crash dialog ───────────────────────────────────────────────
async function showCrashDialog(code) {
  const logContent = readErrorLog();
  const stderr     = stderrBuf.slice(-2000); // last 2000 chars

  let detail = `Exit code: ${code}\n\n`;

  if (logContent) {
    detail += `Error details:\n${logContent.slice(0, 1500)}`;
  } else if (stderr) {
    detail += `Output:\n${stderr}`;
  } else {
    detail += 'No error details captured.\n\n'
      + 'Common causes:\n'
      + '  • Python packages failed to install\n'
      + '  • No audio device / sound card issue\n'
      + '  • Permission error writing to disk\n\n'
      + 'Try running in a terminal:\n'
      + '  python app.py';
  }

  const { response } = await dialog.showMessageBox({
    type: 'error',
    title: 'Gramophone Engine Crashed',
    message: 'The Python backend stopped unexpectedly.',
    detail,
    buttons: ['Quit', 'Try Again'],
    defaultId: 1,
    cancelId: 0,
  });

  if (response === 1) {
    // Restart
    app.relaunch();
    app.quit();
  } else {
    app.quit();
  }
}

// ─── Start Flask ──────────────────────────────────────────────────────────────
async function startFlask() {
  if (!(await isPortFree(PORT))) {
    console.log(`[Electron] Port ${PORT} already in use — assuming Flask is running`);
    return true;
  }

  const python = findPython();
  if (!python) {
    await dialog.showErrorBox(
      'Python Not Found',
      'Gramophone requires Python 3.8 or newer.\n\n'
      + 'Download from: https://python.org\n\n'
      + 'On Windows: make sure to check\n"Add Python to PATH" during installation.\n\n'
      + 'On macOS: run  brew install python3\n'
      + 'On Linux:  run  sudo apt install python3'
    );
    app.quit();
    return false;
  }

  const backendPath = DEV
    ? path.join(__dirname, 'app.py')
    : path.join(process.resourcesPath, 'backend', 'app.py');

  console.log(`[Electron] Backend: ${backendPath}`);

  // Delete stale error log
  try {
    const logPath = DEV
      ? path.join(__dirname, 'gramophone_error.log')
      : path.join(process.resourcesPath, 'backend', 'gramophone_error.log');
    if (fs.existsSync(logPath)) fs.unlinkSync(logPath);
  } catch { /* ignore */ }

  flaskProc = spawn(python, [backendPath], {
    cwd: path.dirname(backendPath),
    env: {
      ...process.env,
      PYTHONUNBUFFERED:   '1',
      PYTHONIOENCODING:  'utf-8',   // prevent emoji crash on Windows cp1252
      PYTHONUTF8:        '1',        // Python 3.7+ UTF-8 mode
      GRAMOPHONE_PORT:   String(PORT),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  flaskProc.stdout.on('data', d => {
    const s = d.toString();
    process.stdout.write(`[Flask] ${s}`);
    // Forward startup log lines to splash screen if loaded
    if (win) {
      try {
        win.webContents.executeJavaScript(
          `typeof updateSplashMsg === 'function' && updateSplashMsg(${JSON.stringify(s.trim())})`
        ).catch(() => {});
      } catch { /* splash may not be loaded */ }
    }
  });

  flaskProc.stderr.on('data', d => {
    const s = d.toString();
    stderrBuf += s;
    process.stderr.write(`[Flask ERR] ${s}`);
  });

  flaskProc.on('error', err => {
    console.error('[Electron] Failed to spawn Python:', err.message);
    stderrBuf += `\nSpawn error: ${err.message}`;
  });

  flaskProc.on('exit', (code, signal) => {
    console.log(`[Electron] Flask exited — code=${code} signal=${signal}`);
    // Only show crash dialog if it wasn't a clean shutdown
    if (code !== 0 && code !== null && signal !== 'SIGTERM') {
      showCrashDialog(code);
    }
  });

  return true;
}

// ─── Wait for Flask to respond ────────────────────────────────────────────────
function waitForFlask() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 60_000; // 60s — first run needs pip installs

    function attempt() {
      // Check if Flask process already died
      if (flaskProc && flaskProc.exitCode !== null && flaskProc.exitCode !== 0) {
        return reject(new Error(`Flask exited with code ${flaskProc.exitCode} before becoming ready.`));
      }

      if (Date.now() > deadline) {
        return reject(new Error(
          'The backend took too long to start (60 seconds).\n\n'
          + 'Possible causes:\n'
          + '  • pip is downloading large packages (try again)\n'
          + '  • Firewall blocking localhost connections\n'
          + '  • Python packages failing to install\n\n'
          + 'Try running in a terminal to see the error:\n'
          + '  python app.py'
        ));
      }

      const req = http.get(`${FLASK_URL}/api/state`, res => {
        res.resume();
        if (res.statusCode < 500) return resolve();
        setTimeout(attempt, 800);
      });
      req.on('error', () => setTimeout(attempt, 800));
      req.setTimeout(1500, () => { req.destroy(); setTimeout(attempt, 800); });
    }

    // Small initial delay to let Python start
    setTimeout(attempt, 500);
  });
}

// ─── Create main window ───────────────────────────────────────────────────────
function createWindow() {
  win = new BrowserWindow({
    width:     1160,
    height:    720,
    minWidth:  900,
    minHeight: 600,
    frame:     false,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    trafficLightPosition: { x: 14, y: 12 },
    backgroundColor: '#1a1108',
    show: false,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
      webSecurity:      false,
    },
  });

  // Show splash immediately
  win.loadFile(path.join(__dirname, 'splash.html'));
  win.once('ready-to-show', () => win.show());

  // Wait for Flask then load real UI
  waitForFlask()
    .then(() => {
      console.log('[Electron] Flask is ready — loading app');
      win.loadURL(FLASK_URL);
    })
    .catch(async err => {
      // Check if it's because Flask already crashed (handled by exit handler)
      if (flaskProc && flaskProc.exitCode !== null && flaskProc.exitCode !== 0) {
        return; // crash dialog already shown
      }
      await dialog.showErrorBox('Startup Failed', err.message);
      app.quit();
    });

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(FLASK_URL)) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ─── IPC: window controls ─────────────────────────────────────────────────────
ipcMain.on('win:minimize',  () => win?.minimize());
ipcMain.on('win:maximize',  () => win?.isMaximized() ? win.unmaximize() : win.maximize());
ipcMain.on('win:close',     () => win?.close());
ipcMain.handle('win:isMax', () => win?.isMaximized() ?? false);

// ─── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  const ok = await startFlask();
  if (ok) createWindow();
  app.on('activate', () => {
    if (!BrowserWindow.getAllWindows().length) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (flaskProc && !flaskProc.killed) {
    console.log('[Electron] Killing Flask...');
    if (process.platform === 'win32') {
      try { execSync(`taskkill /pid ${flaskProc.pid} /f /t`, { stdio: 'ignore' }); } catch { /* ignore */ }
    } else {
      flaskProc.kill('SIGTERM');
    }
    flaskProc = null;
  }
});