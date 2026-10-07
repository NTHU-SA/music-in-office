using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Graphics;

namespace OfficeMusicDesktop;

public sealed partial class MainWindow : Window
{
    private bool _allowClose;
    private bool _closing;

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(nint window);

    public MainWindow()
    {
        InitializeComponent();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "AppIcon.ico"));
        var handle = Win32Interop.GetWindowFromWindowId(AppWindow.Id);
        var scale = GetDpiForWindow(handle) / 96.0;
        var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea;
        int width = Math.Min((int)(780 * scale), (int)(workArea.Width * 0.95));
        int height = Math.Min((int)(860 * scale), (int)(workArea.Height * 0.95));
        AppWindow.MoveAndResize(new RectInt32(
            workArea.X + (workArea.Width - width) / 2,
            workArea.Y + (workArea.Height - height) / 2, width, height));
        RootFrame.Navigate(typeof(MainPage));
        AppWindow.Closing += OnClosing;
    }

    private async void OnClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (_allowClose || RootFrame.Content is not MainPage page) return;
        args.Cancel = true;
        if (_closing) return;
        _closing = true;
        bool wasBusy = page.ViewModel.IsBusy;
        page.ViewModel.IsBusy = true;
        try
        {
            if (!await page.ViewModel.SaveBeforeCloseAsync())
            {
                var confirmation = new ContentDialog
                {
                    XamlRoot = page.XamlRoot,
                    Title = "設定尚未儲存",
                    Content = "這次修改無法儲存。仍要關閉並放棄未儲存的修改嗎？",
                    PrimaryButtonText = "關閉而不儲存",
                    CloseButtonText = "返回設定",
                    DefaultButton = ContentDialogButton.Close
                };
                if (await confirmation.ShowAsync() != ContentDialogResult.Primary) return;
            }
            await page.ViewModel.CloseAsync();
            _allowClose = true;
            Close();
        }
        catch (Exception exception) { page.ViewModel.ShowError(exception); }
        finally
        {
            _closing = false;
            if (!_allowClose) page.ViewModel.IsBusy = wasBusy;
        }
    }
}
