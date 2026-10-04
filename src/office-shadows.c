/*
 * office-shadows.c - hide the MSO_BORDEREFFECT shadow windows of Office apps.
 *
 * Microsoft Office 2013+ draws its window frame shadow with four transparent
 * MSO_BORDEREFFECT_WINDOW_CLASS hWnds (see Microsoft KB 2821007).  Under Wine
 * those windows are rendered as separate top-level windows; on compositors
 * that decorate or blur every window they show up as an ugly blurred border
 * around the Office window (and they steal input on the window edges).
 *
 * Sending
 *
 *     Msg    = WM_USER + 0x0900
 *     wParam = 117  (WM_MSO_WPARAM_OMFRAMEENABLESHADOW)
 *     lParam = 0    (shadows disabled; 1 re-enables)
 *
 * to the application's main window hides those windows.  This is the same
 * switch Microsoft documents in KB 2821007, implemented as a tiny helper that
 * the launcher runs next to every Office application.
 *
 * Usage:
 *   office-shadows.exe            disable shadows once, then exit
 *   office-shadows.exe --watch    keep disabling while Office is running
 *
 * The --watch mode polls every two seconds so shadows stay hidden even if
 * Office recreates its frame windows, and exits shortly after the last Office
 * window disappears so it never keeps a Wine session alive on its own.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

#define WM_MSO (WM_USER + 0x0900)
#define WM_MSO_WPARAM_OMFRAMEENABLESHADOW 117
#define WM_MSO_LPARAM_SHADOW_DISABLED 0

static const char *office_exes[] =
{
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe",
    "msaccess.exe", "mspub.exe", "onenote.exe",
};

static int ci_equal(const char *a, const char *b)
{
    while (*a && *b)
    {
        char ca = *a, cb = *b;
        if (ca >= 'A' && ca <= 'Z') ca += 'a' - 'A';
        if (cb >= 'A' && cb <= 'Z') cb += 'a' - 'A';
        if (ca != cb) return 0;
        a++;
        b++;
    }
    return *a == *b;
}

static int is_office_process(HWND hwnd)
{
    DWORD pid = 0;
    HANDLE proc;
    char path[MAX_PATH];
    DWORD len = sizeof(path);
    const char *base;
    size_t i;

    GetWindowThreadProcessId(hwnd, &pid);
    if (!pid) return 0;

    proc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!proc) return 0;
    if (!QueryFullProcessImageNameA(proc, 0, path, &len))
    {
        CloseHandle(proc);
        return 0;
    }
    CloseHandle(proc);

    base = strrchr(path, '\\');
    base = base ? base + 1 : path;
    for (i = 0; i < sizeof(office_exes) / sizeof(office_exes[0]); i++)
        if (ci_equal(base, office_exes[i])) return 1;
    return 0;
}

static BOOL CALLBACK enum_proc(HWND hwnd, LPARAM lp)
{
    int *count = (int *)lp;
    DWORD_PTR result;

    if (!is_office_process(hwnd)) return TRUE;
    if (!IsWindowVisible(hwnd)) return TRUE;

    SendMessageTimeoutA(hwnd, WM_MSO, WM_MSO_WPARAM_OMFRAMEENABLESHADOW,
                        WM_MSO_LPARAM_SHADOW_DISABLED, SMTO_ABORTIFHUNG, 2000,
                        &result);
    (*count)++;
    return TRUE;
}

int main(int argc, char **argv)
{
    int watch = 0, seen = 0, misses = 0, waits = 0, count, i;

    for (i = 1; i < argc; i++)
    {
        if (!strcmp(argv[i], "--watch")) watch = 1;
        else
        {
            printf("office-shadows: unknown option '%s'\n", argv[i]);
            return 2;
        }
    }

    for (;;)
    {
        count = 0;
        EnumWindows(enum_proc, (LPARAM)&count);

        if (count)
        {
            if (!seen)
                printf("office-shadows: disabled shadows on %d Office window(s)\n", count);
            seen = 1;
            misses = 0;
        }
        else if (seen)
        {
            /* Office windows are gone: stop watching shortly after. */
            if (++misses >= 5) break;
        }
        else if (++waits >= 300)
        {
            /* No Office window appeared within ~10 minutes: give up. */
            break;
        }

        if (!watch) break;
        Sleep(2000);
    }

    return 0;
}
