// musiccil GUI —— 搜索、组歌单、一键丢给终端播放器。
//
// 纯 Win32 API + Common Controls，不依赖 Qt/wxWidgets：一条 g++ 命令就能
// 编出独立 exe。网络走 WinINet，也是系统自带的。
//
// 分工：这个窗口负责"找歌、凑歌单"，真正播歌仍然是终端唱片机（封面主题色、
// 旋转像素封面、歌词那些都在那边）。所以点播放是把歌单交给播放器，
// 而不是在这里自己解码音频。

// 必须定义在 windows.h 之前：MinGW 的 commctrl.h 用这两个版本宏来放开
// ListView 宏和 INITCOMMONCONTROLSEX，不定义的话会直接报"未声明"。
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#ifndef _WIN32_IE
#define _WIN32_IE 0x0600
#endif

#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>

#include <string>
#include <vector>

#include "api.h"
#include "playlist.h"

namespace {

// 控件 id
enum {
    ID_SEARCH = 1001,
    ID_PLATFORM,
    ID_SEARCH_BTN,
    ID_RESULT,
    ID_ADD,
    ID_ADD_ALL,
    ID_REMOVE,
    ID_UP,
    ID_DOWN,
    ID_CLEAR,
    ID_PLAYLIST,
    ID_PLAY,
    ID_SAVE,
    ID_OPEN_FOLDER,
    ID_STATUS,
};

const int kPlatformCodes = 6;
const char* kPlatformCodesArr[] = {"wy", "qq", "kg", "kw", "mg", "qi"};
const char* kPlatformNames[] = {"网易云", "QQ音乐", "酷狗", "酷我", "咪咕", "千千"};

HWND g_main = 0;
HWND g_searchEdit = 0;
HWND g_platform = 0;
HWND g_result = 0;
HWND g_playlistView = 0;
HWND g_status = 0;

MusicApi g_api;
std::vector<Track> g_results;
std::vector<Track> g_playlist;

// 搜索结果里"已加入歌单"的标记：控件不原生支持，用勾在歌名前表示。
bool inPlaylist(const Track& t) {
    for (size_t i = 0; i < g_playlist.size(); i++) {
        if (g_playlist[i].id == t.id && g_playlist[i].type == t.type) return true;
    }
    return false;
}

void setStatus(const std::string& text) {
    SetWindowTextW(g_status, toWide(text).c_str());
}

// 往 ListView 里插一行；ListView 只认宽字符，所以统一在这里转。
void listAdd(HWND list, const std::wstring& a, const std::wstring& b, const std::wstring& c) {
    int row = ListView_GetItemCount(list);
    LVITEMW item;
    ZeroMemory(&item, sizeof(item));
    item.mask = LVIF_TEXT;
    item.iItem = row;
    item.pszText = (LPWSTR)a.c_str();
    int idx = ListView_InsertItem(list, &item);
    ListView_SetItemText(list, idx, 1, (LPWSTR)b.c_str());
    ListView_SetItemText(list, idx, 2, (LPWSTR)c.c_str());
}

void setupList(HWND list) {
    ListView_SetExtendedListViewStyle(list, LVS_EX_FULLROWSELECT | LVS_EX_GRIDLINES);
    LVCOLUMNW col;
    ZeroMemory(&col, sizeof(col));
    col.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_SUBITEM;
    col.pszText = (LPWSTR)L"歌名";
    col.cx = 240;
    ListView_InsertColumn(list, 0, &col);
    col.pszText = (LPWSTR)L"歌手";
    col.cx = 150;
    ListView_InsertColumn(list, 1, &col);
    col.pszText = (LPWSTR)L"专辑";
    col.cx = 160;
    ListView_InsertColumn(list, 2, &col);
}

void refreshResults() {
    ListView_DeleteAllItems(g_result);
    for (size_t i = 0; i < g_results.size(); i++) {
        const Track& t = g_results[i];
        // 已在歌单里的打勾，用户一眼能看出哪些加过了
        std::wstring name = (inPlaylist(t) ? L"√ " : L"   ") + toWide(t.name);
        listAdd(g_result, name, toWide(t.artist), toWide(t.album));
    }
}

void refreshPlaylist() {
    ListView_DeleteAllItems(g_playlistView);
    for (size_t i = 0; i < g_playlist.size(); i++) {
        char num[16];
        snprintf(num, sizeof(num), "%d. ", (int)(i + 1));
        const Track& t = g_playlist[i];
        listAdd(g_playlistView, toWide(std::string(num) + t.name), toWide(t.artist),
                toWide(t.album));
    }
    char buf[64];
    snprintf(buf, sizeof(buf), "歌单 %d 首", (int)g_playlist.size());
    setStatus(buf);
}

std::string currentPlatform() {
    int sel = (int)SendMessage(g_platform, CB_GETCURSEL, 0, 0);
    if (sel < 0 || sel >= kPlatformCodes) return "wy";
    return kPlatformCodesArr[sel];
}

int selectedResult() {
    return ListView_GetNextItem(g_result, -1, LVNI_SELECTED);
}

int selectedPlaylistIndex() {
    return ListView_GetNextItem(g_playlistView, -1, LVNI_SELECTED);
}

void doSearch() {
    wchar_t buf[512];
    GetWindowTextW(g_searchEdit, buf, 512);
    std::string keyword = toUtf8(buf);
    if (keyword.empty()) {
        setStatus("请输入搜索关键词");
        return;
    }
    setStatus("搜索中…");
    // 搜索期间禁用按钮，避免用户连点把请求叠起来
    EnableWindow(GetDlgItem(g_main, ID_SEARCH_BTN), FALSE);
    UpdateWindow(g_main);

    std::string error;
    bool ok = g_api.search(keyword, currentPlatform(), 30, &g_results, &error);
    EnableWindow(GetDlgItem(g_main, ID_SEARCH_BTN), TRUE);
    if (!ok) {
        g_results.clear();
        refreshResults();
        setStatus("搜索失败：" + error);
        return;
    }
    refreshResults();
    char msg[128];
    snprintf(msg, sizeof(msg), "找到 %d 首（双击加入歌单）", (int)g_results.size());
    setStatus(msg);
}

void addSelected() {
    int row = selectedResult();
    if (row < 0 || row >= (int)g_results.size()) {
        setStatus("请先在搜索结果里选一首");
        return;
    }
    const Track& t = g_results[row];
    if (inPlaylist(t)) {
        setStatus("已经在歌单里了");
        return;
    }
    g_playlist.push_back(t);
    refreshPlaylist();
    refreshResults();
}

void addAllResults() {
    if (g_results.empty()) {
        setStatus("还没有搜索结果");
        return;
    }
    int added = 0;
    for (size_t i = 0; i < g_results.size(); i++) {
        if (inPlaylist(g_results[i])) continue;
        g_playlist.push_back(g_results[i]);
        added++;
    }
    refreshPlaylist();
    refreshResults();
    char msg[64];
    snprintf(msg, sizeof(msg), "已加入 %d 首", added);
    setStatus(msg);
}

void removeSelected() {
    int row = selectedPlaylistIndex();
    if (row < 0 || row >= (int)g_playlist.size()) {
        setStatus("请先在歌单里选一首");
        return;
    }
    g_playlist.erase(g_playlist.begin() + row);
    refreshPlaylist();
    refreshResults();
}

void moveSelected(int delta) {
    int row = selectedPlaylistIndex();
    int target = row + delta;
    if (row < 0 || target < 0 || target >= (int)g_playlist.size()) return;
    std::swap(g_playlist[row], g_playlist[target]);
    refreshPlaylist();
    ListView_SetItemState(g_playlistView, target, LVIS_SELECTED | LVIS_FOCUSED,
                          LVIS_SELECTED | LVIS_FOCUSED);
}

void clearPlaylist() {
    if (g_playlist.empty()) return;
    g_playlist.clear();
    refreshPlaylist();
    refreshResults();
    setStatus("歌单已清空");
}

void doSave() {
    if (g_playlist.empty()) {
        setStatus("歌单是空的");
        return;
    }
    std::string path = defaultPlaylistPath();
    std::string error;
    if (savePlaylist(g_playlist, path, &error).empty()) {
        setStatus("保存失败：" + error);
        return;
    }
    setStatus("已保存到 " + path);
}

void doPlay() {
    if (g_playlist.empty()) {
        setStatus("歌单是空的，先加几首");
        return;
    }
    std::string path = defaultPlaylistPath();
    std::string error;
    if (savePlaylist(g_playlist, path, &error).empty()) {
        setStatus("保存失败：" + error);
        return;
    }
    unsigned long pid = launchPlayer(path, "", &error);
    if (pid == 0) {
        setStatus(error);
        MessageBoxW(g_main, toWide(error).c_str(), L"启动播放器失败", MB_ICONERROR | MB_OK);
        return;
    }
    char msg[192];
    snprintf(msg, sizeof(msg),
             "已在终端窗口打开播放器（pid %lu，%d 首）。终端里按 Q 退出播放器。",
             pid, (int)g_playlist.size());
    setStatus(msg);
}

void openPlaylistFolder() {
    std::string path = defaultPlaylistPath();
    std::string dir = path.substr(0, path.find_last_of('\\'));
    ShellExecuteW(g_main, L"open", L"explorer.exe", toWide(dir).c_str(), 0, SW_SHOWNORMAL);
}

// 布局：顶部搜索行 / 中部左右两个列表 / 底部按钮栏 / 状态栏
void layout(HWND hwnd) {
    RECT rc;
    GetClientRect(hwnd, &rc);
    int w = rc.right - rc.left;
    int h = rc.bottom - rc.top;
    int pad = 10;
    int rowH = 26;
    int statusH = 24;
    int btnH = 30;

    int y = pad;
    MoveWindow(g_searchEdit, pad, y, w - 320, rowH, TRUE);
    MoveWindow(g_platform, w - 300, y, 120, 200, TRUE);
    MoveWindow(GetDlgItem(hwnd, ID_SEARCH_BTN), w - 170, y, 80, rowH, TRUE);
    MoveWindow(GetDlgItem(hwnd, ID_ADD), w - 84, y, 74, rowH, TRUE);
    y += rowH + pad;

    int listH = h - y - btnH - statusH - pad * 3;
    if (listH < 80) listH = 80;
    int half = (w - pad * 3) / 2;
    MoveWindow(g_result, pad, y, half, listH, TRUE);
    MoveWindow(g_playlistView, pad * 2 + half, y, half, listH, TRUE);
    y += listH + pad;

    // 按钮栏
    int bx = pad;
    int bw = 92;
    MoveWindow(GetDlgItem(hwnd, ID_ADD_ALL), bx, y, bw, btnH, TRUE); bx += bw + 6;
    MoveWindow(GetDlgItem(hwnd, ID_REMOVE), bx, y, bw, btnH, TRUE); bx += bw + 6;
    MoveWindow(GetDlgItem(hwnd, ID_UP), bx, y, 60, btnH, TRUE); bx += 66;
    MoveWindow(GetDlgItem(hwnd, ID_DOWN), bx, y, 60, btnH, TRUE); bx += 66;
    MoveWindow(GetDlgItem(hwnd, ID_CLEAR), bx, y, 76, btnH, TRUE);

    // 右侧主操作
    int rx = w - pad - 110;
    MoveWindow(GetDlgItem(hwnd, ID_PLAY), rx, y, 110, btnH, TRUE); rx -= 116;
    MoveWindow(GetDlgItem(hwnd, ID_SAVE), rx, y, 110, btnH, TRUE); rx -= 116;
    MoveWindow(GetDlgItem(hwnd, ID_OPEN_FOLDER), rx, y, 110, btnH, TRUE);

    y += btnH + 4;
    MoveWindow(g_status, pad, y, w - pad * 2, statusH - 4, TRUE);
}

HWND makeButton(HWND parent, const wchar_t* text, int id) {
    return CreateWindowExW(0, L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                           0, 0, 0, 0, parent, (HMENU)(INT_PTR)id, 0, 0);
}

void createControls(HWND hwnd) {
    g_searchEdit = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"",
                                   WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
                                   0, 0, 0, 0, hwnd, (HMENU)(INT_PTR)ID_SEARCH, 0, 0);

    g_platform = CreateWindowExW(0, L"COMBOBOX", L"",
                                 WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL,
                                 0, 0, 0, 0, hwnd, (HMENU)(INT_PTR)ID_PLATFORM, 0, 0);
    for (int i = 0; i < kPlatformCodes; i++) {
        SendMessageW(g_platform, CB_ADDSTRING, 0, (LPARAM)toWide(kPlatformNames[i]).c_str());
    }
    SendMessage(g_platform, CB_SETCURSEL, 0, 0);

    makeButton(hwnd, L"搜索", ID_SEARCH_BTN);
    makeButton(hwnd, L"加入 >>", ID_ADD);
    makeButton(hwnd, L"全部加入", ID_ADD_ALL);
    makeButton(hwnd, L"移出", ID_REMOVE);
    makeButton(hwnd, L"上移", ID_UP);
    makeButton(hwnd, L"下移", ID_DOWN);
    makeButton(hwnd, L"清空", ID_CLEAR);
    makeButton(hwnd, L"播放", ID_PLAY);
    makeButton(hwnd, L"保存歌单", ID_SAVE);
    makeButton(hwnd, L"打开目录", ID_OPEN_FOLDER);

    g_result = CreateWindowExW(WS_EX_CLIENTEDGE, WC_LISTVIEWW, L"",
                               WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL,
                               0, 0, 0, 0, hwnd, (HMENU)(INT_PTR)ID_RESULT, 0, 0);
    g_playlistView = CreateWindowExW(WS_EX_CLIENTEDGE, WC_LISTVIEWW, L"",
                                     WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL,
                                     0, 0, 0, 0, hwnd, (HMENU)(INT_PTR)ID_PLAYLIST, 0, 0);
    setupList(g_result);
    setupList(g_playlistView);

    g_status = CreateWindowExW(0, L"STATIC", L"就绪：输入关键词 → 搜索 → 加入歌单 → 播放",
                               WS_CHILD | WS_VISIBLE | SS_LEFT,
                               0, 0, 0, 0, hwnd, (HMENU)(INT_PTR)ID_STATUS, 0, 0);
}

// EnumChildWindows 要的是 __stdcall 回调，C++ 的 lambda 默认是 cdecl，
// 直接传会编译不过；用回调转发给它。
BOOL CALLBACK setFontProc(HWND child, LPARAM lp) {
    SendMessageW(child, WM_SETFONT, (WPARAM)lp, TRUE);
    return TRUE;
}

// 字体：默认的系统字体在高 DPI 下很糊，统一换成微软雅黑 9pt
void applyFont(HWND hwnd) {
    HFONT font = CreateFontW(-14, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                             OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                             DEFAULT_PITCH | FF_DONTCARE, L"Microsoft YaHei UI");
    if (!font) return;
    EnumChildWindows(hwnd, setFontProc, (LPARAM)font);
    SendMessageW(hwnd, WM_SETFONT, (WPARAM)font, TRUE);
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE:
            createControls(hwnd);
            applyFont(hwnd);
            return 0;

        case WM_SIZE:
            if (g_searchEdit) layout(hwnd);
            return 0;

        case WM_GETMINMAXINFO: {
            // 再小就摆不下两个列表和按钮栏了
            MINMAXINFO* mmi = (MINMAXINFO*)lp;
            mmi->ptMinTrackSize.x = 760;
            mmi->ptMinTrackSize.y = 460;
            return 0;
        }

        case WM_COMMAND: {
            int id = LOWORD(wp);
            int code = HIWORD(wp);
            if (id == ID_SEARCH_BTN && code == BN_CLICKED) doSearch();
            else if (id == ID_ADD && code == BN_CLICKED) addSelected();
            else if (id == ID_ADD_ALL && code == BN_CLICKED) addAllResults();
            else if (id == ID_REMOVE && code == BN_CLICKED) removeSelected();
            else if (id == ID_UP && code == BN_CLICKED) moveSelected(-1);
            else if (id == ID_DOWN && code == BN_CLICKED) moveSelected(1);
            else if (id == ID_CLEAR && code == BN_CLICKED) clearPlaylist();
            else if (id == ID_PLAY && code == BN_CLICKED) doPlay();
            else if (id == ID_SAVE && code == BN_CLICKED) doSave();
            else if (id == ID_OPEN_FOLDER && code == BN_CLICKED) openPlaylistFolder();
            return 0;
        }

        case WM_NOTIFY: {
            LPNMHDR nh = (LPNMHDR)lp;
            // 双击搜索结果 = 加入歌单（比先选中再点按钮顺手）
            if (nh->code == NM_DBLCLK && nh->idFrom == ID_RESULT) addSelected();
            return 0;
        }

        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

}  // namespace

int WINAPI WinMain(HINSTANCE inst, HINSTANCE, LPSTR, int show) {
    // 让回车别在输入框里发两声 beep
    INITCOMMONCONTROLSEX icc;
    icc.dwSize = sizeof(icc);
    icc.dwICC = ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES;
    InitCommonControlsEx(&icc);

    WNDCLASSEXW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursor(0, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = L"MusiccilGuiWindow";
    if (!RegisterClassExW(&wc)) return 1;

    // 按屏幕大小开一个合适的初始窗口，别超出工作区
    int w = 1040, h = 640;
    int sw = GetSystemMetrics(SM_CXSCREEN);
    int sh = GetSystemMetrics(SM_CYSCREEN);
    if (w > sw - 80) w = sw - 80;
    if (h > sh - 120) h = sh - 120;
    HWND hwnd = CreateWindowExW(
        0, wc.lpszClassName, L"musiccil — 搜索 · 组歌单 · 终端播放",
        WS_OVERLAPPEDWINDOW, (sw - w) / 2, (sh - h) / 2, w, h, 0, 0, inst, 0);
    if (!hwnd) return 1;

    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);
    g_main = hwnd;
    layout(hwnd);

    MSG msg;
    while (GetMessageW(&msg, 0, 0, 0) > 0) {
        // 搜索框里按回车直接搜
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_RETURN &&
            GetFocus() == g_searchEdit) {
            doSearch();
            continue;
        }
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
