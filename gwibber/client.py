
"""

Gwibber Client v1.0
SegPhault (Ryan Paul) - 01/05/2008

"""

import sys, time, os, threading, mx.DateTime, hashlib
import gtk, gtk.glade, gobject, table, webkit, simplejson
import microblog, gwui, config, gintegration, configui

# Setup Pidgin
import pidgin
microblog.PROTOCOLS["pidgin"] = pidgin

gtk.gdk.threads_init()

MAX_MESSAGE_LENGTH = 140

CONFIGURABLE_UI_ELEMENTS = ["editor", "statusbar", "tray_icon"]
IMAGE_CACHE_DIR = "%s/.gwibber/imgcache" % os.path.expanduser("~")
VERSION_NUMBER = 0.7

DEFAULT_PREFERENCES = {
  "version": VERSION_NUMBER,
  "show_notifications": True,
  "refresh_interval": 2,
  "minimize_to_tray": False,
  "hide_taskbar_entry": False,
  "spellcheck_enabled": True,
}

for i in CONFIGURABLE_UI_ELEMENTS:
  DEFAULT_PREFERENCES["show_%s" % i] = True

class GwibberClient(gtk.Window):
  def __init__(self, ui_dir="ui"):
    gtk.Window.__init__(self, gtk.WINDOW_TOPLEVEL)
    self.set_title("Gwibber")
    self.set_default_size(330, 500)
    config.GCONF.add_dir(config.GCONF_PREFERENCES_DIR, config.gconf.CLIENT_PRELOAD_NONE)
    self.preferences = config.Preferences()
    self.ui_dir = ui_dir
    self.last_update = None
    self.last_clear = None
    layout = gtk.VBox()

    gtk.rc_parse_string("""
    style "tab-close-button-style" {
      GtkWidget::focus-padding = 0
      GtkWidget::focus-line-width = 0
      xthickness = 0
      ythickness = 0
     }
     widget "*.tab-close-button" style "tab-close-button-style"
     """)

    self.accounts = configui.AccountManager(ui_dir=ui_dir)
    self.client = microblog.Client(self.accounts)
    self.client.handle_error = self.handle_error
    self.client.post_process_message = self.post_process_message

    self.notification_bubbles = {}
    self.message_target = None
    
    self.errors = table.generate([
      ["date", lambda t: t.time.strftime("%Y-%m-%d")],
      ["time", lambda t: t.time.strftime("%I:%M:%S %p")],
      ["username"],
      ["protocol"],
      ["message", (gtk.CellRendererText(), {
        "markup": lambda t: t.message})]
    ])

    self.connect("delete-event", self.on_window_close)

    for key, value in DEFAULT_PREFERENCES.items():
      if self.preferences[key] == None: self.preferences[key] = value

    self.timer = gobject.timeout_add(60000 * int(self.preferences["refresh_interval"]), self.update)
    self.preferences.notify("refresh_interval", self.on_refresh_interval_changed)

    gtk.icon_theme_add_builtin_icon("gwibber", 22,
      gtk.gdk.pixbuf_new_from_file_at_size("%s/gwibber.svg" % ui_dir, 24, 24))

    self.set_icon_name("gwibber")
    self.tray_icon = gtk.status_icon_new_from_icon_name("gwibber")
    self.tray_icon.connect("activate", self.on_toggle_window_visibility)

    self.tabs = gtk.Notebook()
    self.tabs.set_scrollable(True)
    self.add_tab(self.client.receive, "Messages", show_icon = "go-home")
    self.add_tab(self.client.responses, "Replies", show_icon = "mail-reply-all")
    #self.add_map_tab(self.client.friend_positions, "Location")

    if gintegration.SPELLCHECK_ENABLED:
      self.input = gintegration.sexy.SpellEntry()
      self.input.set_checked(self.preferences["spellcheck_enabled"])
    else: self.input = gtk.Entry()
    self.input.connect("populate-popup", self.on_input_context_menu)
    self.input.connect("activate", self.on_input_activate)
    self.input.connect("changed", self.on_input_change)
    self.input.set_max_length(140)

    self.cancel_button = gtk.Button("Cancel")
    self.cancel_button.connect("clicked", self.on_cancel_reply)

    self.editor = gtk.HBox()
    self.editor.pack_start(self.input)
    self.editor.pack_start(self.cancel_button, False)
    
    vb = gtk.VBox(spacing=5)
    vb.pack_start(self.tabs, True, True)
    vb.pack_start(self.editor, False, False)
    vb.set_border_width(5)
    
    self.statusbar = gtk.Statusbar()

    layout.pack_start(self.setup_menus(), False)
    layout.pack_start(vb, True, True)
    layout.pack_start(self.statusbar, False)
    self.add(layout)

    if gintegration.can_notify:
      import dbus

      def on_notify_close(nId):
        if self.notification_bubbles.has_key(nId):
          del self.notification_bubbles[nId]

      def on_notify_action(nId, action):
        if action == "reply":
          self.reply(self.notification_bubbles[nId])
          self.window.show()
          self.present()
      
      bus = dbus.SessionBus()
      bus.add_signal_receiver(on_notify_close,
        dbus_interface="org.freedesktop.Notifications",
        signal_name="CloseNotification")
      
      bus.add_signal_receiver(on_notify_action,
        dbus_interface="org.freedesktop.Notifications",
        signal_name="ActionInvoked")

    for i in CONFIGURABLE_UI_ELEMENTS:
      config.GCONF.notify_add(config.GCONF_PREFERENCES_DIR + "/show_%s" % i,
        lambda *a: self.apply_ui_element_settings())
    
    config.GCONF.notify_add("/apps/gwibber/accounts", self.on_account_change)

    self.preferences.notify("hide_taskbar_entry",
      lambda *a: self.apply_ui_element_settings())

    self.preferences.notify("spellcheck_enabled",
      lambda *a: self.apply_ui_element_settings())
    
    #for i in CONFIGURABLE_UI_SETTINGS:
    #  config.GCONF.notify_add(config.GCONF_PREFERENCES_DIR + "/%s" % i,
    #    lambda *a: self.apply_ui_drawing_settings())
    
    self.show_all()
    self.apply_ui_element_settings()
    self.cancel_button.hide()

    if not self.preferences["inhibit_startup_refresh"]:
      self.update()

  def on_search(self, *a):
    dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT, gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK, None)

    entry = gtk.Entry()
    entry.connect("activate", lambda *a: dialog.response(gtk.RESPONSE_OK))

    dialog.set_markup("Enter a search query:")
    dialog.vbox.pack_end(entry, True, True, 0)
    dialog.show_all()
    ret = dialog.run()
    dialog.hide()

    query = entry.get_text()
    view = self.add_tab(
      lambda: self.client.search(query), query, True, gtk.STOCK_FIND)
    
    self.update([view.get_parent()])
    
  def add_tab(self, data_handler, text, show_close = False, show_icon = None):
    view = gwui.MessageView(self.ui_dir, "default")
    view.link_handler = self.on_link_clicked
    view.data_retrieval_handler = data_handler
    view.config_retrieval_handler = self.get_account_config

    scroll = gtk.ScrolledWindow()
    scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
    scroll.add(view)

    img = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)

    btn = gtk.Button()
    btn.set_image(img)
    btn.set_relief(gtk.RELIEF_NONE)
    btn.set_name("tab-close-button")

    hb = gtk.HBox(spacing=2)
    if show_icon:
      hb.pack_start(gtk.image_new_from_icon_name(show_icon, gtk.ICON_SIZE_MENU))
    hb.pack_start(gtk.Label(text))
    if show_close: hb.pack_end(btn, False, False)
    hb.show_all()
    
    self.tabs.append_page(scroll, hb)
    self.tabs.set_tab_reorderable(scroll, True)
    self.tabs.show_all()

    btn.connect("clicked", lambda w: self.tabs.remove_page(self.tabs.page_num(view)))
    return view

  def add_map_tab(self, data_handler, text, show_close = True, show_icon = "applications-internet"):
    view = gwui.MapView(self.ui_dir)
    view.link_handler = self.on_link_clicked
    view.data_retrieval_handler = data_handler
    view.config_retrieval_handler = self.get_account_config

    scroll = gtk.Frame()
    #scroll.set_policy(gtk.POLICY_NEVER, gtk.POLICY_NEVER)
    scroll.add(view)

    img = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)

    btn = gtk.Button()
    btn.set_image(img)
    btn.set_relief(gtk.RELIEF_NONE)
    btn.set_name("tab-close-button")

    hb = gtk.HBox(spacing=2)
    if show_icon:
      hb.pack_start(gtk.image_new_from_icon_name(show_icon, gtk.ICON_SIZE_MENU))
    hb.pack_start(gtk.Label(text))
    if show_close: hb.pack_end(btn, False, False)
    hb.show_all()
    
    self.tabs.append_page(scroll, hb)
    self.tabs.set_tab_reorderable(scroll, True)
    self.tabs.show_all()

    btn.connect("clicked", lambda w: self.tabs.remove_page(self.tabs.page_num(view)))
    return view

  def theme_preview_test(self, *a):
    themes = [gwui.ThemeSelector(self.ui_dir, t) for t in ["default", "funkatron"]]

    hb = gtk.HBox(spacing=5)
    for t in themes: hb.pack_start(t.widgets)
    for t in themes[1:]: t.selector.set_group(themes[0].selector)
    
    def testit(*a):
      for t in themes:
        t.content.load_messages(self.message_store)
        t.content.load_preferences(self.get_account_config())

    b = gtk.Button("Test")
    b.connect("clicked", testit)

    w = gtk.Window()
    w.set_title("Theme Selector")
    w.set_border_width(5)

    vb = gtk.VBox(spacing=5)
    vb.pack_start(hb)
    vb.pack_start(b, False, False)
    w.add(vb)

    w.show_all()

  def on_account_change(self, client, junk, entry, *args):
    if "color" in entry.get_key():
      for tab in self.tabs.get_children():
        view = tab.get_child()
        view.load_preferences(self.get_account_config())

  def on_window_close(self, w, e):
    if self.preferences["minimize_to_tray"]:
      self.preferences["show_tray_icon"] = True
      self.on_toggle_window_visibility(w)
      return True
    else: gtk.main_quit()
  
  def on_cancel_reply(self, w):
    self.cancel_button.hide()
    self.message_target = None
    self.input.set_text("")

  def on_toggle_window_visibility(self, w):
    if self.get_property("visible"):
      self.last_position = self.get_position()
      self.hide()
    else:
      self.show()
      self.move(*self.last_position)

  def apply_ui_element_settings(self):
    for i in CONFIGURABLE_UI_ELEMENTS:
      if hasattr(self, i):
        getattr(self, i).set_property(
          "visible", self.preferences["show_%s" % i])
    
    self.set_property("skip-taskbar-hint",
      self.preferences["hide_taskbar_entry"])

    if gintegration.SPELLCHECK_ENABLED:
      self.input.set_checked(self.preferences["spellcheck_enabled"])

  def on_refresh_interval_changed(self, *a):
    gobject.source_remove(self.timer)
    self.timer = gobject.timeout_add(
      60000 * int(self.preferences["refresh_interval"]), self.update)

  def copy_to_tomboy(self, message):
    gintegration.create_tomboy_note("%s message from %s at %s\n\n%s" % (
      message.account["protocol"].capitalize(),
      message.sender, message.time, message.text))
  
  def reply(self, message):
    acct = message.account

    if acct.supports(microblog.can.REPLY):
      self.input.grab_focus()
      self.input.set_text("@%s: " % message.sender_nick)
      self.input.set_position(-1)

      self.message_target = message.account
      self.cancel_button.show()

    """
    if acct["protocol"] in microblog.PROTOCOLS.keys():
      if hasattr(message.client, "can_reply"):
        view = self.add_tab(lambda: self.client.thread(message), "Jaiku Replies", True)
        view.load_messages()
        view.load_preferences(self.get_account_config())
    """
    
  def on_link_clicked(self, uri, view):
    if uri.startswith("gwibber:"):
      if uri.startswith("gwibber:reply"):
        self.reply(view.message_store[int(uri.split("/")[-1])])
        return True
      elif uri.startswith("gwibber:search"):
        query = uri.split("/")[-1]
        self.add_tab(lambda: self.client.search(query), query, True, gtk.STOCK_FIND)
        return True
    else: return False

  def on_input_context_menu(self, obj, menu):
    menu.append(gtk.SeparatorMenuItem())
    for acct in self.accounts:
      if acct["protocol"] in microblog.PROTOCOLS.keys():
        if acct.supports(microblog.can.SEND):
          mi = gtk.CheckMenuItem("%s (%s)" % (acct["username"],
            acct.get_protocol().PROTOCOL_INFO["name"]))
          acct.bind(mi, "send_enabled")
          menu.append(mi)

    menu.show_all()

  def on_input_change(self, widget):
    self.statusbar.pop(1)
    if len(widget.get_text()) > 0:
      self.statusbar.push(1, "Characters remaining: %s" % (
        widget.get_max_length() - len(unicode(widget.get_text(), "utf-8"))))

  def load_messages_into_view(self, view):
    msgs = simplejson.dumps([dict(m.__dict__, message_index=n)
      for n, m in enumerate(view.message_store)], indent=4, default=str)

    view.execute_script("addMessages(%s)" % msgs)
    self.set_account_colors(view)

  def on_theme_change(self, w):
    def on_load_finished(*a):
      self.load_messages_into_view(self.content)      

    self.content.connect("load-finished", on_load_finished)
    self.content.load_theme("funkatron")

  def on_accounts_menu(self, amenu):
    amenu.emit_stop_by_name("select")
    menu = amenu.get_submenu()
    for c in menu: menu.remove(c)
    
    menuAccountsManage = gtk.MenuItem("_Manage")
    menuAccountsManage.connect("activate", lambda *a: self.accounts.show_account_list())
    menu.append(menuAccountsManage)
   
    menuAccountsCreate = gtk.MenuItem("_Create")
    menu.append(menuAccountsCreate)
    mac = gtk.Menu()

    for p in microblog.PROTOCOLS.keys():
      mi = gtk.MenuItem("%s" % microblog.PROTOCOLS[p].PROTOCOL_INFO["name"])
      mi.connect("activate", self.accounts.on_account_create, p)
      mac.append(mi)

    menuAccountsCreate.set_submenu(mac)
    menu.append(gtk.SeparatorMenuItem())
    
    for acct in self.accounts:
      if acct["protocol"] in microblog.PROTOCOLS.keys():
        sm = gtk.Menu()
        
        for i in ["receive", "send"]:
          if acct.supports(getattr(microblog.can, i.upper())):
            mi = gtk.CheckMenuItem("_%s Messages" % i.capitalize())
            acct.bind(mi, "%s_enabled" % i)
            sm.append(mi)
        
        sm.append(gtk.SeparatorMenuItem())
        
        mi = gtk.ImageMenuItem(gtk.STOCK_PROPERTIES)
        mi.connect("activate", lambda w, a: self.accounts.show_properties_dialog(a), acct)
        sm.append(mi)

        if hasattr(acct.get_protocol(), "account_name"):
          aname = acct.get_protocol().account_name(acct)
        elif acct["username"]: aname = acct["username"]
        else: aname = None

        mi = gtk.MenuItem("%s (%s)" % (aname, acct.get_protocol().PROTOCOL_INFO["name"]))
        mi.set_submenu(sm)
        menu.append(mi)
    menu.show_all()
    amenu.set_submenu(menu)

  def setup_menus(self):
    menuGwibber = gtk.Menu()
    menuView = gtk.Menu()
    menuAccounts = gtk.Menu()
    menuHelp = gtk.Menu()
    menuTray = gtk.Menu()

    accelGroup = gtk.AccelGroup()
    self.add_accel_group(accelGroup)

    def create_action(name, accel, stock, fn, parent = menuGwibber):
      mi = gtk.Action("gwibber%s" % name, "_%s" % name, None, stock)
      gtk.accel_map_add_entry("<Gwibber>/%s" % name, *gtk.accelerator_parse(accel))
      mi.set_accel_group(accelGroup)
      mi.set_accel_path("<Gwibber>/%s" % name)
      mi.connect("activate", fn)
      parent.append(mi.create_menu_item())
      return mi

    actRefresh = create_action("Refresh", "<ctrl>R", gtk.STOCK_REFRESH, self.on_refresh) 
    actSearch = create_action("Search", "<ctrl>F", gtk.STOCK_FIND, self.on_search) 
    actClear = create_action("Clear", "<ctrl>L", gtk.STOCK_CLEAR, self.on_clear) 
    actPreferences = create_action("Preferences", "<ctrl>P", gtk.STOCK_PREFERENCES, self.on_preferences) 
    actQuit = create_action("Quit", "<ctrl>Q", gtk.STOCK_QUIT, self.on_quit) 
    
    #actThemeTest = gtk.Action("gwibberThemeTest", "_Theme Test", None, gtk.STOCK_PREFERENCES)
    #actThemeTest.connect("activate", self.theme_preview_test)
    #menuHelp.append(actThemeTest.create_menu_item())

    actAbout = gtk.Action("gwibberAbout", "_About", None, gtk.STOCK_ABOUT)
    actAbout.connect("activate", self.on_about)
    menuHelp.append(actAbout.create_menu_item())

    for i in CONFIGURABLE_UI_ELEMENTS:
      mi = gtk.CheckMenuItem("_%s" % " ".join(i.split("_")).capitalize())
      self.preferences.bind(mi, "show_%s" % i)
      menuView.append(mi)

    if gintegration.SPELLCHECK_ENABLED:
      mi = gtk.CheckMenuItem("S_pellcheck", True)
      self.preferences.bind(mi, "spellcheck_enabled")
      menuView.append(mi)

    mi = gtk.MenuItem("E_rrors")
    mi.connect("activate", self.on_errors_show)
    menuView.append(gtk.SeparatorMenuItem())
    menuView.append(mi)

    menuGwibberItem = gtk.MenuItem("_Gwibber")
    menuGwibberItem.set_submenu(menuGwibber)

    menuViewItem = gtk.MenuItem("_View")
    menuViewItem.set_submenu(menuView)

    menuAccountsItem = gtk.MenuItem("_Accounts")
    menuAccountsItem.set_submenu(menuAccounts)
    menuAccountsItem.connect("select", self.on_accounts_menu)

    menuHelpItem = gtk.MenuItem("_Help")
    menuHelpItem.set_submenu(menuHelp)

    self.throbber = gtk.Image()
    menuSpinner = gtk.ImageMenuItem("")
    menuSpinner.set_right_justified(True)
    menuSpinner.set_sensitive(False)
    menuSpinner.set_image(self.throbber)

    menuTray.append(actRefresh.create_menu_item())
    menuTray.append(actPreferences.create_menu_item())
    menuTray.append(actQuit.create_menu_item())

    self.tray_icon.connect("popup-menu", lambda i,b,a: menuTray.popup(
      None, None, gtk.status_icon_position_menu, b, a, self.tray_icon))
    
    menubar = gtk.MenuBar()
    menubar.append(menuGwibberItem)
    menubar.append(menuViewItem)
    menubar.append(menuAccountsItem)
    menubar.append(menuHelpItem)
    menubar.append(menuSpinner)
    return menubar

  def on_quit(self, *a):
    gtk.main_quit()

  def on_refresh(self, *a):
    self.update()

  def on_about(self, mi):
    glade = gtk.glade.XML("%s/preferences.glade" % self.ui_dir)
    dialog = glade.get_widget("about_dialog")
    dialog.set_version(str(VERSION_NUMBER))
    dialog.connect("response", lambda *a: dialog.hide())
    dialog.show_all()

  def on_clear(self, mi):
    self.last_clear = mx.DateTime.gmt()
    for tab in self.tabs.get_children():
      view = tab.get_child()
      view.execute_script("clearMessages()")
  
  def on_errors_show(self, mi):
    errorwin = gtk.Window()
    errorwin.set_title("Errors")
    errorwin.set_border_width(10)
    errorwin.resize(600, 300)

    def on_row_activate(tree, path, col):
      w = gtk.Window()
      w.set_title("Debug Output")
      w.resize(800, 800)
      
      text = gtk.TextView()
      text.get_buffer().set_text(tree.get_selected().error)

      scroll = gtk.ScrolledWindow()
      scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
      scroll.add_with_viewport(text)

      w.add(scroll)
      w.show_all()

    errors = table.View(self.errors.tree_style,
      self.errors.tree_store, self.errors.tree_filter)
    errors.connect("row-activated", on_row_activate)

    scroll = gtk.ScrolledWindow()
    scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
    scroll.add_with_viewport(errors)

    buttons = gtk.HButtonBox()
    buttons.set_layout(gtk.BUTTONBOX_END)

    def on_click_button(w, stock):
      if stock == gtk.STOCK_CLOSE:
        errorwin.destroy()
      elif stock == gtk.STOCK_CLEAR:
        self.errors.tree_store.clear()

    for stock in [gtk.STOCK_CLEAR, gtk.STOCK_CLOSE]:
      b = gtk.Button(stock=stock)
      b.connect("clicked", on_click_button, stock)
      buttons.pack_start(b)
    
    vb = gtk.VBox(spacing=5)
    vb.pack_start(scroll)
    vb.pack_start(buttons, False, False)

    errorwin.add(vb)
    errorwin.show_all()

  def on_preferences(self, mi):
    glade = gtk.glade.XML("%s/preferences.glade" % self.ui_dir)
    dialog = glade.get_widget("pref_dialog")
    dialog.show_all()

    for widget in ["show_notifications", "refresh_interval", "minimize_to_tray", "hide_taskbar_entry"]:
      self.preferences.bind(glade.get_widget("pref_%s" % widget), widget)

    glade.get_widget("button_close").connect("clicked", lambda *a: dialog.destroy())
  
  def handle_error(self, acct, err, msg = None):
    self.errors += {
      "time": mx.DateTime.gmt(),
      "username": acct["username"],
      "protocol": acct["protocol"],
      "message": "%s\n<i><span foreground='red'>%s</span></i>" % (msg, err.split("\n")[-2]),
      "error": err,
    }

  def on_input_activate(self, e):
    if self.input.get_text().strip():
      if self.message_target:
        protocols = [self.message_target["protocol"]]
      else: protocols = microblog.PROTOCOLS.keys()
    
      self.client.send(self.input.get_text().strip(), protocols)
      self.on_cancel_reply(None)

      self.input.set_text("")

  def post_process_message(self, message):
    if hasattr(message, "image"):
      message.image_url = message.image
      message.image_path = gwui.image_cache(message.image_url)
      message.image = "file://%s" % message.image_path

    message.gId = hashlib.sha1(message.text[:128].strip(".")).hexdigest()
    message.aId = message.account.id

    if self.last_update:
      message.is_new = message.time > self.last_update
    else: message.is_new = False
    
    message.time_string = microblog.support.generate_time_string(message.time)

    if not hasattr(message, "html_string"):
      message.html_string = '<span class="text">%s</span>' % \
        microblog.support.LINK_PARSE.sub('<a href="\\1">\\1</a>', message.text)

    return message

  def get_account_config(self):
    for acct in self.accounts:
      if acct["protocol"] in microblog.PROTOCOLS:
        data = {"id": acct.id, "username": acct["username"], "protocol": acct["protocol"]}
        for c in acct.get_protocol().PROTOCOL_INFO["config"]:
          if "color" in c:
            if acct[c]: color = gtk.gdk.color_parse(acct[c])
            else: color = gtk.gdk.color_parse("#72729f9fcfcf")
            data[c] = {"red": color.red/255, "green": color.green/255, "blue": color.blue/255}
        yield data

  def show_notification_bubbles(self, data):
    for message in data:
      if message.is_new and self.preferences["show_notifications"] and gintegration.can_notify:
        gtk.gdk.threads_enter()
        n = gintegration.notify(message.sender, message.text, hasattr(message,
          "image_path") and message.image_path or None, ["reply", "Reply"])
        gtk.gdk.threads_leave()

        self.notification_bubbles[n] = message

  def flag_duplicates(self, data):
    seen = []
    for message in data:
      message.is_duplicate = message.gId in seen
      if not message.is_duplicate: seen.append(message.gId)
  
  def update(self, tabs = None):
    self.throbber.set_from_animation(gtk.gdk.PixbufAnimation("%s/progress.gif" % self.ui_dir))
    self.target_tabs = tabs

    def process():
      try:

        if not self.target_tabs:
          self.target_tabs = self.tabs.get_children()

        for tab in self.target_tabs:
          view = tab.get_child()
          view.message_store = [m for m in
            view.data_retrieval_handler() if m.time > self.last_clear]
          self.flag_duplicates(view.message_store)
          self.show_notification_bubbles(view.message_store)

        gtk.gdk.threads_enter()
        for tab in self.target_tabs:
          view = tab.get_child()
          view.load_messages()
          view.load_preferences(self.get_account_config())
        gtk.gdk.threads_leave()

        self.statusbar.pop(0)
        self.statusbar.push(0, "Last update: %s" % time.strftime("%I:%M:%S %p"))
        self.last_update = mx.DateTime.gmt()
        
      finally: gobject.idle_add(self.throbber.clear)
    
    t = threading.Thread(target=process)
    t.setDaemon(True)
    t.start()

    return True

if __name__ == '__main__':
  w = GwibberClient()
  w.connect("destroy", gtk.main_quit)
  gtk.main()

