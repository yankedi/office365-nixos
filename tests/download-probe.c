/* Exercise BITS range downloads and check service survival after each job. */
#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <bits.h>
#include <bits2_0.h>
#include <stdio.h>
#include <stdlib.h>

static int transfer(IBackgroundCopyManager *manager, const WCHAR *url,
                    const WCHAR *destination, unsigned iteration)
{
    GUID id;
    IBackgroundCopyJob *job = NULL;
    IBackgroundCopyJob3 *ranged = NULL;
    BG_FILE_RANGE ranges[64];
    HRESULT hr;
    ULONGLONG start = GetTickCount64();
    int result = 1;
    hr = IBackgroundCopyManager_CreateJob(manager, L"office-download-check",
                                          BG_JOB_TYPE_DOWNLOAD, &id, &job);
    printf("iteration=%u CreateJob=%08lx\n", iteration, hr);
    if (FAILED(hr)) return 1;
    hr = IBackgroundCopyJob_QueryInterface(job, &IID_IBackgroundCopyJob3, (void **)&ranged);
    if (FAILED(hr)) goto done;
    for (unsigned i = 0; i < 64; ++i)
    {
        ranges[i].InitialOffset = 33554432ULL + (ULONGLONG)i * 1048576;
        ranges[i].Length = 65536;
    }
    hr = IBackgroundCopyJob3_AddFileWithRanges(ranged, url, destination, 64, ranges);
    if (FAILED(hr)) goto done;
    IBackgroundCopyJob_SetNotifyFlags(job, BG_NOTIFY_DISABLE);
    if (FAILED(IBackgroundCopyJob_Resume(job))) goto done;
    while (GetTickCount64() - start < 30000)
    {
        BG_JOB_STATE state;
        hr = IBackgroundCopyJob_GetState(job, &state);
        if (FAILED(hr)) { printf("GetState=%08lx\n", hr); break; }
        if (state == BG_JOB_STATE_TRANSFERRED)
        {
            hr = IBackgroundCopyJob_Complete(job);
            printf("Complete=%08lx\n", hr);
            result = FAILED(hr);
            break;
        }
        if (state == BG_JOB_STATE_ERROR || state == BG_JOB_STATE_TRANSIENT_ERROR)
        {
            IBackgroundCopyError *error = NULL;
            if (SUCCEEDED(IBackgroundCopyJob_GetError(job, &error)))
            {
                BG_ERROR_CONTEXT context;
                HRESULT code;
                IBackgroundCopyError_GetError(error, &context, &code);
                printf("JobError=%08lx context=%u\n", code, context);
                IBackgroundCopyError_Release(error);
            }
            break;
        }
        Sleep(100);
    }
done:
    if (result) IBackgroundCopyJob_Cancel(job);
    if (ranged) IBackgroundCopyJob3_Release(ranged);
    IBackgroundCopyJob_Release(job);
    DeleteFileW(destination);
    return result;
}

int wmain(int argc, WCHAR **argv)
{
    IBackgroundCopyManager *manager = NULL;
    HRESULT hr;
    int result = 0;
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc != 3) return 2;
    if (FAILED(CoInitializeEx(NULL, COINIT_MULTITHREADED))) return 2;
    hr = CoCreateInstance(&CLSID_BackgroundCopyManager, NULL, CLSCTX_LOCAL_SERVER,
                          &IID_IBackgroundCopyManager, (void **)&manager);
    printf("CoCreateInstance=%08lx\n", hr);
    if (SUCCEEDED(hr))
    {
        for (unsigned i = 0; i < 3; ++i)
        {
            IEnumBackgroundCopyJobs *jobs = NULL;
            if (transfer(manager, argv[1], argv[2], i)) result = 1;
            Sleep(1200);
            hr = IBackgroundCopyManager_EnumJobs(manager, 0, &jobs);
            printf("ServiceAfterTransfer=%08lx\n", hr);
            if (jobs) IEnumBackgroundCopyJobs_Release(jobs);
            if (FAILED(hr)) { result = 3; break; }
        }
        IBackgroundCopyManager_Release(manager);
    }
    else result = 2;
    CoUninitialize();
    return result;
}
