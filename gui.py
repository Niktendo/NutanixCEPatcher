#
# Copyright (c) 2013 Nutanix Inc. All rights reserved.
#
# Author: thomas@nutanix.com
#
# Installer GUI.
#

from __future__ import print_function
import curses
import os
import glob
import platform
import time
import re
import shutil
import sys
from uuid import uuid4, UUID

from hardware_inventory import disk_info
import shell
import sysUtil
import minimum_reqs
from consts import (PHOENIX_VERSION, IMAGES_DIR, factory_exchange_dir,
                    ValidationError, ARCH_PPC, DRIVER_PACKAGE_NAME, DRIVERS_DIR,
                    MAX_DISK_SERIAL, MAX_DEV, MAX_MODEL, MAX_TYPE, MAX_SZ)
from gui_widgets import (BaseCheckBox, CursesControl, BaseTextViewBlock,
                         BaseElementHandler, Button, RadioButton, TextEditor,
                         FakeText)
from factory_workflow import fatal_exc_handler
from param_list import ParamList
from log import (ERROR, set_log_fatal_callback, disable_ttyout_handler,
                 enable_ttyout_handler)
from gui_actions import (
    INSTALL_HYPERVISOR, CONFIGURE_HYPERVISOR, INSTALL_SVM, REPAIR_SVM,
    determine_actions, get_hypervisor_images_for_action,
    get_nos_images_for_action)
from gui_review import get_review_content
from images import (get_packaged_nos, get_packaged_hyp, get_nos_from_cvm,
                    gui_message, HypervisorImages)
from layout.layout_finder import get_layout
from layout.layout_tools import get_possible_boot_devices_from_layout
from shared_functions import validate_and_correct_network_addresses

XC6320_BANNER = \
"""\033[1m\033[5m
                      \b** IMPORTANT - READ CAREFULLY **\033[0m
Use the following screens to complete Dell factory installation of the XC6320
system. Refer to the Dell XC6320 Owner's Manual for information on system and
chassis terminology and Service Tag locations. You must provide correct values
during the following steps as they are critical to system setup and you cannot
change them after they are entered.  Only set up one system at a time.
The setup fields include:

Node Position:
The Node Position is set to the chassis slot number for this system. Use the
chassis slot numbers on the front of the chassis and the system identification
indicator LEDs to identify this system's chassis slot number if it is not
automatically detected.

Block ID:
The Block ID is set to the chassis Service Tag located on the top of left-hand
rack ear of the chassis. Set the Block ID to the same value for each system
residing in a chassis. Verify Chassis Service Tag and update if blank or
incorrect. The chassis Service Tag is *NOT* the system Service Tag.

Node Serial:
The Node Serial is set to the system Service Tag.  Do *NOT* change this value.
"""

# This would be set to True if Phoenix detects the platform supports one node
# clusters.
one_node_cluster = False
network_setup = False

class GuiParams(object):
  def __init__(self):
    self.node_position_choices = None
    self.node_positions = None
    self.node_models = None
    self.allowed_actions = None
    self.node_position_detected = False
    self.node_serial_detected = False
    self.block_id_detected = False
    self.cluster_id_detected = False
    self.check_boxes_locked = False
    self.factory_config = None
    self.svm_data = False
    self.p_list = ParamList()

gp = GuiParams()
gui = None


class CheckBox(BaseCheckBox):

  def draw(self):
    color = 0
    if self.focus:
      color = curses.color_pair(3)
    if self.selected:
      self.window.addstr(self.y,self.x,"[x]",color)
      deselect_checkbox(self.deselect_if_checked)
      set_entity_usable(True, self.disable_if_unchecked)
      set_entity_visible(True, self.hide_if_unchecked)
    else:
      self.window.addstr(self.y,self.x,"[ ]",color)
      deselect_checkbox(self.deselect_if_unchecked)
      set_entity_usable(False, self.disable_if_unchecked)
      set_entity_visible(False, self.hide_if_unchecked)
    self.window.addstr(self.y,self.x+4,self.label,0)

  def keystroke(self,c):
    if gp.check_boxes_locked:
      return self.handler.NOTHING
    if c == ord(' '):
      self.selected = not self.selected
      self.draw()
    return self.handler.NOTHING


def noop_callback(_):
  return


# Choices are a list of tuples of type (text,metadata)
class DropDown(CursesControl):

  def __init__(self, window, y, x, label, choices, selectedIndex, toggled=False,
               callback_on_change=noop_callback, breakpoint=40):
    CursesControl.__init__(self)
    self.window = window
    self.x = x
    self.y = y
    self.label = label
    self.choices = choices
    self.selectedIndex = selectedIndex
    self.toggled = toggled
    self.callback_on_change = callback_on_change
    self._width = 0
    self.breakpoint = breakpoint

  @property
  def width(self):
    for text, metadata in self.choices:
      if (len(text)) > self._width:
        self._width = len(text)
    return self._width

  def draw(self):
    color = 0
    if self.focus:
      color = curses.color_pair(3)
    text = self.choices[self.selectedIndex][0]
    # Indicate a field is a dropdown for usability (ENG-11168)
    if len(self.choices) > 1:
      text = "< " + text
      text += " >"
    else:
      text += "\t "

    # Expand text to be the size of the maximum field.
    text += " " * (self.width - len(text))
    self.window.addstr(self.y, self.x, self.label, 0)

    if self.width > self.breakpoint:
      def find_break_point(text):
        breakpoint = text.rfind(" ", 0, len(text))
        if breakpoint == -1:
          breakpoint = self.breakpoint
          return breakpoint
        elif breakpoint <= self.breakpoint:
          return breakpoint
        else:
          return find_break_point(text[0:breakpoint])
      break_point = find_break_point(text)
      texts = [text[0:break_point], text[break_point:]]
    else:
      texts = [text]

    y = self.y
    for text in texts:
      text += " " * (self.breakpoint - len(text))
      self.window.addstr(y, self.x + len(self.label), text, color)
      y = y + 1

  def keystroke(self, c):
    if c == curses.KEY_RIGHT:
      self.toggled = True
      self.selectedIndex += 1
      self.selectedIndex = self.selectedIndex % len(self.choices)
      self.draw()
      self.callback_on_change(self)
      return self.handler.HANDLED
    if c == curses.KEY_LEFT:
      self.toggled = True
      self.selectedIndex -= 1
      self.selectedIndex = self.selectedIndex % len(self.choices)
      self.draw()
      self.callback_on_change(self)
      return self.handler.HANDLED
    return self.handler.NOTHING

  def get_selected_data(self, index=1):
    return self.choices[self.selectedIndex][index]

  def set_choices(self, new_choices):
    self.choices = new_choices
    self.selectedIndex = 0
    self.draw()
    self.callback_on_change(self)


class TextViewBlock(BaseTextViewBlock):
  """
  TextViewBlock reads a file and displays the contents in a scroll-able block.
  """
  def __init__(self, window, y, x, filename, text, label, width, height, margin):
    BaseTextViewBlock.__init__(self, window, y, x, filename,
                               text, label, width, height, margin)

  def draw(self):
    y = self.y
    banner = '*' * ((self.width - len(self.label)) // 2)
    if (self.width - len(self.label)) % 2 == 1:
      b2 = '*'
    else:
      b2 = ''
    self.window.addstr(y,self.x,banner + self.label + banner + b2, self.width)
    y += 1
    for line in range(self.ycursor, self.ycursor+self.usable_height):
      txt = ''
      if len(self.text) > line:
        txt = self.text[line]
      if self.ycursor != 0 and line == self.ycursor:
        edges = '^'
      elif (self.ycursor + self.usable_height < len(self.text) and
            line == self.ycursor + self.usable_height - 1):
        edges = 'V'
      else:
        edges = '|'
      nblanks = self.usable_width - len(txt)
      margin = ' ' * self.margin
      self.window.addstr(y, self.x, edges + margin + txt + self.blanks[0:nblanks]
                         + margin + edges, self.width)
      y += 1
    self.window.addstr(y,self.x,'*' * self.width,self.width)
    #y += 1
    #statusmsg = 'ycur: %d' % self.ycursor
    #self.window.addstr(y,self.x,statusmsg,len(statusmsg))

  def keystroke(self,c):
    if c == curses.KEY_UP:
      self.ycursor -= 1
    elif c == curses.KEY_DOWN:
      self.ycursor += 1
    elif c == curses.KEY_PPAGE:
      self.ycursor -= self.usable_height - 1
    elif c == curses.KEY_NPAGE:
      self.ycursor += self.usable_height - 1
    else:
      return self.handler.NOTHING
    self.sanitize_ycursor()
    #try:
    self.draw()
    #except IndexError, e:
    #  print self.ycursor
    #  print e
    #  raise e
    return self.handler.HANDLED

class ChoiceSelectBlock(CursesControl):
  """
  ChoiceSelectBlock displays a list of options in a scroll-able block.
  """
  def __init__(self, window, y, x, choices, current, label, width, height,
               keys=None):
    CursesControl.__init__(self)
    self.window = window
    self.y = y
    self.x = x
    self.choices = choices
    self.label = label
    self.keys = keys
    self.set_keystroke_handler()

    if len(self.keys) != len(self.choices):
      raise Exception('Number of keys (%d) must match number of choices'
        ' (%d).' % (len(keys), len(choices)))
    if current and current not in self.choices and current not in self.keys:
      raise Exception('Current choice "%s" not in choice list.' % current)
    self.lastkey = 0

    if width < len(self.label) + 2:
      raise Exception('ChoiceSelectBlock width must be at least 2 '
                          'characters wider than the label.')
    if width < 4:
      raise Exception('ChoiceSelectBlock width must be >= 4.')
    self.width = width

    self.set_cursor('* ', ' *')

    if height < 3:
      raise Exception('ChoiceSelectBlock height must be >= 3.')
    self.height = height
    self.usable_height = height - 2
    for txt in self.choices:
      if len(txt) > self.usable_width:
        raise Exception('Option "%s" is wider than allowed (%d/%d).' % (txt,
          len(txt), self.usable_width))
    if current:
      idx = (self.choices.index(current) if current in self.choices
        else self.keys.index(current))
      # top
      if idx < self.usable_height:
        self.ytop = 0
        self.wincursor = idx
      # bottom
      elif idx >= len(self.choices) - self.usable_height:
        self.ytop = len(self.choices) - self.usable_height
        self.wincursor = idx - self.ytop
      # middle
      else:
        self.wincursor = self.usable_height / 2
        self.ytop = idx - self.wincursor
    else:
      self.ytop = 0
      self.wincursor = 0
    self.blanks = ' ' * self.usable_width

  def set_cursor(self, left, right):
    self.left_cursor = left
    self.right_cursor = right
    self.usable_width = self.width - (2 + len(left) + len(right))

  def set_keystroke_handler(self, keystrokes=[], handler=None):
    self.keystrokes = keystrokes
    self.keystroke_handler = handler

  def get_selected_data(self):
    if self.keys:
      return self.keys[self.ytop + self.wincursor]
    return self.choices[self.ytop + self.wincursor]

  def draw(self):
    y = self.y
    banner = '*' * ((self.width - len(self.label)) // 2)
    if (self.width - len(self.label)) % 2 == 1:
      b2 = '*'
    else:
      b2 = ''
    self.window.addstr(y,self.x,banner + self.label + banner + b2, self.width)
    y += 1
    #self.window.addstr(y,self.x,'ytop: %d, wincur: %d, usable_height: %d, selected: %s%s' %
    #                   (self.ytop, self.wincursor, self.usable_height, self.get_selected_data(), ' '*20))
    #y += 1
    for line in range(self.ytop, self.ytop + self.usable_height):
      if line < len(self.choices):
        #txt = '%d/%d: ' % (line, len(self.choices))
        txt = self.choices[line]
      else:
        #txt = '%d' % (line)
        txt = ''
      if line == self.ytop + self.wincursor:
        txt = self.left_cursor + txt + self.right_cursor
      else:
        txt = (' '*len(self.left_cursor)) + txt + (' '*len(self.right_cursor))
      l = len(txt)
      if self.ytop != 0 and line == self.ytop:
        edges = '^'
      elif (self.ytop + self.usable_height < len(self.choices) and
            line == self.ytop + self.usable_height - 1):
        edges = 'V'
      else:
        edges = '|'
      self.window.addstr(y,self.x,edges + txt + edges,self.width)
      y += 1
      #self.window.addstr(y, self.x, 'usable_width: %d, l: %d' %
      #                   (self.usable_width, l))
      #y += 1
    self.window.addstr(y,self.x,'*' * self.width,self.width)

  def sanitize_ycursor(self):
    # assumption: we only ever adjust wincursor by 1
    if self.wincursor < 0:
      self.ytop -= 1
      self.wincursor = 0
    if self.wincursor >= self.usable_height:
      self.ytop += 1
      self.wincursor = self.usable_height - 1
    # check ytop after wincursor in case we adjusted it beyond bounds
    if self.ytop < 0:
      self.ytop = 0
    if self.ytop + self.wincursor >= len(self.choices):
      self.ytop = len(self.choices) - self.usable_height
      self.wincursor = self.usable_height - 1

  def keystroke(self,c):
    must_draw = False
    if self.keystroke_handler:
      must_draw = self.keystroke_handler(c, ping=True)
    self.lastkey = c
    if c == curses.KEY_UP:
      self.wincursor -= 1
    elif c == curses.KEY_DOWN:
      self.wincursor += 1
    elif c == curses.KEY_PPAGE:
      self.ytop -= self.usable_height
    elif c == curses.KEY_NPAGE:
      self.ytop += self.usable_height
    elif c in self.keystrokes:
      self.keystroke_handler(c)
    else:
      if must_draw:
        self.draw()
      return self.handler.NOTHING
    self.sanitize_ycursor()
    self.draw()
    return self.handler.HANDLED

class ElementHandler(BaseElementHandler):
  """
  Handles list of elements, focus and distributes keyboard events
  """
  NOTHING = 0
  EXIT = 1
  NEXT = 2
  HANDLED = 3

  def __init__(self, window):
    BaseElementHandler.__init__(self, window)

  def process(self):
    y = 0
    while 1:
      self.window.refresh()
      c = self.window.getch()
      # self.window.addstr(y,0,str(c)+"   ")
      y = (y+1) % 10
      current_index = self.get_focused_element_index()
      current_ele = self.elements[current_index]
      action = current_ele.keystroke(c)
      if action == self.EXIT:
        self.lastControl = current_ele
        return
      elif action == self.NEXT:
        while not self.elements[newIndex].accepts_focus:
          newIndex = (newIndex+1) % len(self.elements)
        newIndex = (current_index+1) % len(self.elements)
        while not self.elements[newIndex].accepts_focus:
          newIndex = (newIndex+1) % len(self.elements)
        self.elements[newIndex].set_focus(True)
      elif action == self.HANDLED:
        pass
      else:
        if c == 9 or c == 10 or c == curses.KEY_DOWN or c == curses.KEY_RIGHT:
          newIndex = (current_index+1) % len(self.elements)
          while not self.elements[newIndex].accepts_focus:
            newIndex = (newIndex+1) % len(self.elements)
          self.elements[newIndex].set_focus(True)
        elif c == curses.KEY_UP or c == curses.KEY_LEFT:
          newIndex = (current_index-1) % len(self.elements)
          while not self.elements[newIndex].accepts_focus:
            newIndex = (newIndex-1) % len(self.elements)
          self.elements[newIndex].set_focus(True)

class LocaleGui(object):
  class LocaleParams(object):
    def __init__(self):
      self.locale = None

    def validate(self):
      return bool(self.locale)

  def __init__(self):
    self.skip_get_params = True
    self.isFirst = True
    self.page = 0
    self.finalPage = 1
    ret,out,err = shell.shell_cmd(["/bin/localectl list-keymaps"])
    if ret or err:
      raise Exception('Could not retrieve list of keymaps.')
    self.kb_layouts = out.split('\n')
    ret,out,err = shell.shell_cmd(
      ["/bin/localectl | grep 'VC Keymap' | awk '{print $3}'"])
    if ret or err:
      raise Exception('Could not determine current keymap.')
    self.kb_current = out.strip()
    self.ce_drives = self.get_drive_list()

  def get_drive_list(self):
    import sysUtil
    import minimum_reqs
    disks = disk_info.collect_disk_info()
    boot_disk = sysUtil.find_boot_disk(None)
    if boot_disk:
      if boot_disk.dev in disks:
        del disks[boot_disk.dev]
    else:
      raise Exception("Could not identify boot disk.")
    try:
      minimum_reqs.CE_checkDisks(boot_dev=boot_disk)
      return None
    except minimum_reqs.MinimumRequirementsError:
      return list(disks.values())

  def get_extra_params(self):
    lp = self.LocaleParams()
    lp.locale = self.kdb_layout.get_selected_data()
    return lp

  def proceedPage(self, ignore):
    if self.page == 0:
      pass
    # add additional page logic above
    elif self.page == self.finalPage:
      return self.handler.EXIT
    else:
      ERROR("finalPage was not updated.")
      sys.exit(1)

    self.page += 1
    self.handler.clear()
    self.window.clear()
    y, x = self.init_header()
    self.init_page(y,x)
    self.stdscr.refresh()
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def init_ui(self, stdscr):
    y,x = stdscr.getmaxyx()
    self.max_y = y-4
    self.max_x = x-6

    #self.window = stdscr.subwin(y-2,x-4,1,2)
    #self.window.bkgdset(' ', curses.color_pair(2))
    # a la ENG-95857 we have to compensate for an ncurses bug
    w = stdscr.subwin(y-2, x-2, 1, 1)
    w.bkgdset(' ', curses.color_pair(2))
    w.clear()
    y, x = w.getmaxyx()
    self.window = w.subwin(y-1, x-1, 1, 1)

    self.window.clear()
    self.window.border()
    self.window.keypad(1)



    self.proceedPage(None)

  def init_header(self):
    y = 1
    x = 5

    self.window.addnstr(y,x,"<< Nutanix Community Edition Installer >>",41)
    y += 2

    return y, x

  def init_page(self, y, x):
    if self.page != 1:
      return

    self.window.addstr(y,x,"Please select your keyboard layout from the following list.")
    y += 2

    self.kdb_layout = ChoiceSelectBlock(self.window,y,x,
                               self.kb_layouts, self.kb_current,
                               "Keyboard Layout", 50, 10)
    self.handler.add(self.kdb_layout)
    y += 11

    if self.ce_drives:
      self.window.addstr(y,x, "WARNING: Destructive IO tests will be run on the"
        " following disks in order to confirm acceptable performance.")
      y += 1
      self.window.addstr(y,x, "         If the disks listed below still have any"
        " data on them, please cancel and backup your data first.")
      y += 2

      disk_text = []
      longest_disk = 0
      for disk in self.ce_drives:
        d = " %s: Model [%s], Size [%.2f] GB, Serial [%s]" % (disk.dev,
             disk.model, disk.size, disk.serial)
        if len(d) > self.max_x-2:
          d = d[:self.max_x-5]
          d += '...'
        disk_text.append(d)
        if len(d) > longest_disk:
          longest_disk = len(d)
      height = min(len(disk_text)+2,10)
      self.diskwarning = TextViewBlock(self.window,y,x,None,disk_text,'Disks',
                                       longest_disk+3,height)
      self.handler.add(self.diskwarning,bool(height>8))
      y += height + 1

    cancelButton = Button(self.window,y,x,"Cancel",lambda e:ElementHandler.EXIT)
    self.handler.add(cancelButton)
    self.proceedButton = Button(self.window,y,x+10,"Proceed", self.proceedPage)
    self.handler.add(self.proceedButton)

  def interactive_ui(self, stdscr):
    self.stdscr = stdscr
    if self.isFirst:
      curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
      curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
      curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_RED)

      stdscr.bkgdset(' ', curses.color_pair(1))
      stdscr.clear()
      stdscr.border()

    if self.isFirst:
      self.init_ui(stdscr)
      self.isFirst = False

    self.handler.elements[0].set_focus(True)

    self.handler.process()

    if self.page < self.finalPage:
      return False
    else:
      return self.handler.lastControl == self.proceedButton

class CEGui(object):
  def __init__(self):
    self.skip_get_params = False
    self.isFirst = True
    self.page = 0
    self.finalPage = 2
    self.DISK_MODEL_LENGTH = 20

  def get_extra_params(self):
    ahv_ver = os.path.basename(get_packaged_hyp()[0].path)[:-4]
    if self.hyp_select.get_selected() == 'AHV ({})'.format(ahv_ver):
      self.hypervisor = get_packaged_hyp()[0]
    elif self.hyp_select.get_selected() == 'ESXi':
      gp.p_list.esx_path = self.hyp_esx_path.get_displayed_text()
      self.hypervisor = HypervisorImages(gp.p_list.esx_path, "esx")
    gp.p_list.host_ip = self.host_ip.get_displayed_text()
    gp.p_list.host_subnet_mask = self.host_subnet_mask.get_displayed_text()
    gp.p_list.default_gw = self.default_gw.get_displayed_text()
    gp.p_list.svm_ip = self.svm_ip.get_displayed_text()
    gp.p_list.svm_subnet_mask = self.svm_subnet_mask.get_displayed_text()
    gp.p_list.svm_default_gw = self.svm_default_gw.get_displayed_text()
    gp.p_list.create_1node_cluster = self.create_1node.selected
    if self.create_1node.selected:
      gp.p_list.dns_ip = self.dns_ip.get_displayed_text()
    else:
      gp.p_list.dns_ip = ''

    gp.p_list.ce_eula_accepted = self.acceptbox.selected
    gp.p_list.ce_eula_viewed = self.eula.scrolled_to_end

  def previousPage(self, ignore):
    self.page -= 1
    self.handler.clear()
    self.window.clear()
    y, x = self.init_header()
    self.init_page(y, x)
    self.stdscr.refresh()
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def proceedPage(self, ignore):
    if self.page == 0:
      pass
    elif self.page == 1:
      pass
    # add additional page logic above
    elif self.page == self.finalPage:
      return self.handler.EXIT
    else:
      ERROR("finalPage was not updated.")
      sys.exit(1)

    self.page += 1
    self.handler.clear()
    self.window.clear()
    y, x = self.init_header()
    self.init_page(y,x)
    self.stdscr.refresh()
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def init_ui(self, stdscr):
    y,x = stdscr.getmaxyx()
    self.max_y = y-4
    self.max_x = x-6

    #self.window = stdscr.subwin(y-2,x-4,1,2)
    #self.window.bkgdset(' ', curses.color_pair(2))
    # a la ENG-95857 we have to compensate for an ncurses bug
    w = stdscr.subwin(y-2, x-2, 1, 1)
    w.bkgdset(' ', curses.color_pair(2))
    w.clear()
    y, x = w.getmaxyx()
    self.window = w.subwin(y-1, x-1, 1, 1)

    self.window.clear()
    self.window.border()
    self.window.keypad(1)

    self.handler = ElementHandler(self.window)

    self.proceedPage(None)

  def init_header(self):
    y = 1
    x = 5

    self.window.addnstr(y, x-2, "<< Nutanix Community Edition Installer - AOS %s >>" %
                        get_packaged_nos()[0].version,
                        48 + len(get_packaged_nos()[0].version))
    y += 2

    if gp.node_position_detected and gp.block_id_detected:
      self.window.addnstr(
          y, x, "WARNING: Nutanix software is or was already installed", 53)
      y += 1
      self.window.addnstr(
          y, x, "on the local drives. Proceeding will reformat the drives,", 57)
      y += 1
      self.window.addnstr(y, x,
                          "erasing any data currently present on this node.",
                          48)
      y += 2

    return y, x

  def disk_custom_keys(self):
    return [ord('h'), ord('c'), ord('d'), ord('R')]

  def disk_custom_keystroke_handler(self, c, ping=False):
    # ping is a special event from keystroke() that always happens
    # in addition to the possible call for the custom handler keystroke
    # return specifies whether the element must be re-drawn, even if the
    # keystroke is not handled (i.e. navigating to next element)
    if ping:
      if self.disk_select.temp_status:
        self.disk_select.temp_status = None
        if c not in self.disk_custom_keys():
          # we won't get another callback
          self.update_disk_usage()
        return True
      return False

    disk = self.disk_select.get_selected_data()
    if disk in self.iso_disks and c != ord('R'):
      self.disk_select.temp_status = 'ISO installer disk(s) cannot be used as a destination.'

    elif c == ord('h'):
      self.hyp_boot_disk = [disk]
      if disk in self.cvm_boot_disk:
        self.cvm_boot_disk.remove(disk)
      if disk in self.cvm_data_disks:
        self.cvm_data_disks.remove(disk)

    elif c == ord('c'):
      if not self.disks[disk].isSSD:
        self.disk_select.temp_status = 'CVM boot disk(s) must be SSDs.'
      elif self.disks[disk].size < 200.0:
        self.disk_select.temp_status = 'CVM boot disk(s) must be at least 200 GB in size.'
      elif disk in self.cvm_boot_disk:
        self.cvm_boot_disk.remove(disk)
      else:
        if len(self.cvm_boot_disk) in [0,1]:
          self.cvm_boot_disk.append(disk)
        else:
          # could try to match disks by size
          # for now just replace one in rolling fashion
          del self.cvm_boot_disk[0]
          self.cvm_boot_disk.append(disk)
        if disk in self.hyp_boot_disk:
          self.hyp_boot_disk.remove(disk)
        if disk in self.cvm_data_disks:
          self.cvm_data_disks.remove(disk)

    elif c == ord('d'):
      if self.disks[disk].size < 200.0:
        self.disk_select.temp_status = 'Data disk(s) must be at least 200 GB in size.'
      elif disk in self.cvm_data_disks:
        self.cvm_data_disks.remove(disk)
      else:
        self.cvm_data_disks.append(disk)
        if disk in self.hyp_boot_disk:
          self.hyp_boot_disk.remove(disk)
        if disk in self.cvm_boot_disk:
          self.cvm_boot_disk.remove(disk)

    elif c == ord('R'):
      disk_defaults = disk_info.choose_ce_disk_defaults(self.disks)
      # don't have to worry about errors here as this was already done once
      self.hyp_boot_disk = disk_defaults['HYP_BOOT']
      self.cvm_boot_disk = disk_defaults['CVM_BOOT']
      self.cvm_data_disks = disk_defaults['CVM_DATA']

    self.update_disk_usage()

  def update_disk_usage(self):
    for i in range(0, len(self.disk_select.keys)):
      disk = self.disk_select.keys[i]
      usage = ' '
      if disk in self.iso_disks:
        usage = 'I'
      elif disk in self.hyp_boot_disk:
        usage = 'H'
      elif disk in self.cvm_boot_disk:
        usage = 'C'
      elif disk in self.cvm_data_disks:
        usage = 'D'
      self.disk_select.choices[i] = self.disk_select.choices[i][:-2] + usage + ']'

    if self.disk_select.temp_status:
      status = self.disk_select.temp_status
      self.disk_select.temp_status = None
    elif not self.hyp_boot_disk:
      status = "Installation cannot proceed without selecting a hypervisor boot disk."
    elif not self.cvm_boot_disk:
      status = "Installation cannot proceed without selecting one (or two) CVM boot disk(s)."
    elif not self.cvm_data_disks:
      status = "Installation cannot proceed without selecting one or more data disks."
    else:
      status = "Hypervisor Boot: {}, CVM Boot: {}, Data: {}".format(
                 self.hyp_boot_disk, self.cvm_boot_disk, self.cvm_data_disks)
    _, width = self.window.getmaxyx()
    status += ' ' * (width - len(status) - self.disk_select.x)
    self.window.addstr(self.disk_select.y + self.disk_select.height,
      self.disk_select.x, status)

  def init_page(self, y, x):
    if self.page == 2:
      self.init_header()
      self.eula = TextViewBlock(self.window, y, x, "ce_eula.txt", None, 'CE EULA', 60, 24, 1)
      self.handler.add(self.eula)
      y += 24
      self.acceptbox = CheckBox(self.window, y, x,
                                "I accept the end user license agreement.  "
                                "(Spacebar to toggle)",
                                False)
      self.handler.add(self.acceptbox)
      y += 2

      x -= 2
      #cancelButton = Button(self.window, y, x, "Cancel", lambda e: ElementHandler.EXIT)
      #self.handler.add(cancelButton)
      self.previousButton = Button(self.window, y, x, "Previous Page", self.previousPage)
      self.handler.add(self.previousButton)
      self.startButton = Button(self.window, y, x + 17, "Start", self.proceedPage)
      self.handler.add(self.startButton)
      y += 1
    elif self.page == 1:
      gp.p_list.esx_path = ''
      gp.p_list.host_ip = ''
      gp.p_list.host_subnet_mask = ''
      gp.p_list.default_gw = ''
      gp.p_list.svm_ip = ''
      gp.p_list.svm_subnet_mask = ''
      gp.p_list.svm_default_gw = ''
      gp.p_list.dns_ip = ''

      # self.window.addstr(y,x,"NOTE: Leaving the IP information below blank will trigger",57)
      # y += 1
      # self.window.addstr(y,x,"the use of DHCP, which is not recommended unless the IP",55)
      # y += 1
      # self.window.addstr(y,x,"addresses are assigned statically in your DHCP server.",54)
      # y += 2
      self.window.addstr(y, x, "Select Hypervisor:", 18)
      y += 1
      ahv_ver = os.path.basename(get_packaged_hyp()[0].path)[:-4]
      hyp_opts = ['AHV ({})'.format(ahv_ver), 'ESXi']
      self.hyp_select = RadioButton(self.window, y, x, hyp_opts)
      y += len(hyp_opts) - 1
      self.hyp_esx_path = TextEditor(self.window, y, x + 12,
                                     "ISO URL:",
                                     gp.p_list.esx_path,
                                     45)
      y += 2
      self.handler.add(self.hyp_select)
      self.handler.add(self.hyp_esx_path, accepts_focus=False, visible=False)
      self.hyp_select.visible_on_opt('ESXi', [self.hyp_esx_path])

      # DISK SELECTION HERE
      self.disks = disk_info.collect_disk_info(skip_part_info=False)
      # DEBUG
      """
      import copy
      self.disks['sdd'] = copy.copy(self.disks['sda'])
      self.disks['sdd'].dev = 'sdd'
      self.disks['sde'] = copy.copy(self.disks['sda'])
      self.disks['sde'].dev = 'sde'
      self.disks['sdf'] = copy.copy(self.disks['sdb'])
      self.disks['sdf'].dev = 'sdf'
      self.disks['nvme0n2'] = copy.copy(self.disks['nvme0n1'])
      self.disks['nvme0n2'].dev = 'nvme0n2'
      self.disks['nvme0n3'] = copy.copy(self.disks['nvme0n1'])
      self.disks['nvme0n3'].dev = 'nvme0n3'
      self.disks['nvme0n4'] = copy.copy(self.disks['nvme0n1'])
      self.disks['nvme0n4'].dev = 'nvme0n4'
      self.disks['nvme0n5'] = copy.copy(self.disks['nvme0n1'])
      self.disks['nvme0n5'].dev = 'nvme0n5'
      """
      # END DEBUG
      disk_defaults = disk_info.choose_ce_disk_defaults(self.disks)
      if "error" in disk_defaults:
        self.iso_disks = []
        self.hyp_boot_disk = []
        self.cvm_boot_disk = []
        self.cvm_data_disks = []
      else:
        self.iso_disks = disk_defaults['PHOENIX_ISO']
        self.hyp_boot_disk = disk_defaults['HYP_BOOT']
        self.cvm_boot_disk = disk_defaults['CVM_BOOT']
        self.cvm_data_disks = disk_defaults['CVM_DATA']
      disk_selection_height = min(8, max(3, len(self.disks)+2))

      keys = []
      choices = []
      width = 20
      for disk in self.disks.values():
        fmt = "{dev} {model} {sn} {sz} GB {usb} {type} [ ]".format(
          dev = disk.dev[:MAX_DEV].ljust(MAX_DEV),
          model = disk.model[:MAX_MODEL].ljust(MAX_MODEL),
          sn = disk.serial[:MAX_DISK_SERIAL].ljust(
            MAX_DISK_SERIAL) if disk.serial else "".ljust(MAX_DISK_SERIAL),
          sz = str(disk.size)[:MAX_SZ].rjust(MAX_SZ),
          usb = "USB" if disk.isUSB else "   ",
          type = "SSD" if disk.isSSD else "HDD"
        )
        keys.append(disk.dev)
        choices.append(fmt)
        width = max(width, len(fmt))
      width += 10 # 2 + len(cursors)
      header_label = "*** {dev} {model} {sn} {sz}    {type} [Use] *".format(
        dev = "[Device]".ljust(MAX_DEV),
        model = "[Model]".ljust(MAX_MODEL),
        sn = "[Serial]".ljust(MAX_DISK_SERIAL),
        sz = "[Size]".ljust(MAX_SZ),
        type = "[Type]".ljust(MAX_TYPE)
      )

      # Disk selection layout
      self.window.addstr(y, x, "Boot disk selection UI (defaults have been selected):", 53)
      y += 1
      self.window.addstr(y, x, "To change, scroll to the disk and press the key 'h' for hypervisor boot, 'c' for CVM", 84)
      y += 1
      self.window.addstr(y, x, "boot or 'd' for data. Press 'R' at any time to reset to default disk selections.", 80)
      y += 2
      label = " Disk Selection :: {} Devices Found ".format(len(self.disks))
      if (width - len(label)) % 2 == 1:
        label += '*'
      banner = '*' * ((width - len(label)) // 2)
      self.window.addstr(y, x, banner + label + banner)
      y += 1
      self.disk_select = ChoiceSelectBlock(self.window, y, x, choices, keys[0],
                                           header_label, width,
                                           disk_selection_height, keys)
      self.disk_select.set_cursor("==> ", " <==")
      self.disk_select.set_keystroke_handler(self.disk_custom_keys(),
                                             self.disk_custom_keystroke_handler)
      self.disk_select.temp_status = None
      self.update_disk_usage()
      self.handler.add(self.disk_select)
      y += disk_selection_height + 2
      # End disk selection


      self.host_ip = TextEditor(self.window,y,x,
                                    "Host IP Address       :",
                                    gp.p_list.host_ip, 15)
      self.handler.add(self.host_ip)
      y += 1

      self.svm_ip = TextEditor(self.window,y,x,
                                    "CVM IP Address        :",
                                    gp.p_list.svm_ip, 15)
      self.handler.add(self.svm_ip)
      y += 1

      self.host_subnet_mask = TextEditor(self.window,y,x,
                                    "Subnet Mask           :",
                                    gp.p_list.host_subnet_mask, 15)
      self.handler.add(self.host_subnet_mask)
      self.svm_subnet_mask = self.host_subnet_mask
      y += 1

      self.default_gw = TextEditor(self.window,y,x,
                                    "Gateway               :",
                                    gp.p_list.default_gw, 15)
      self.handler.add(self.default_gw)
      self.svm_default_gw = self.default_gw
      y += 2

      self.dns_ip = TextEditor(self.window,y,x+35,
                                    "DNS Server:",
                                    gp.p_list.dns_ip,15)
      # FIX FOR 1-NODE CLUSTER STATE PERSISTENCE
      is_1node = getattr(gp.p_list, 'create_1node_cluster', False)
      self.create_1node = CheckBox(self.window,y,x,
                                   "Create single-node cluster?",
                                   is_1node,
                                   disable_if_unchecked=[self.dns_ip],
                                   hide_if_unchecked=[self.dns_ip])
      self.handler.add(self.create_1node)
      self.handler.add(self.dns_ip,accepts_focus=False,visible=False)
      y += 1

      self.action = set([INSTALL_HYPERVISOR, INSTALL_SVM])
      self.nos = get_packaged_nos()[0]

      self.position = FakeText('A')
      self.block_id =  FakeText(str(uuid4()).split('-')[0])
      self.node_serial = FakeText(gp.p_list.node_serial)
      self.cluster_id = FakeText(gp.p_list.cluster_id)
      self.svm_gb_ram = FakeText(gp.p_list.svm_gb_ram)
      x -= 2
      y += 1

      #cancelButton = Button(self.window,y,x,"Cancel",lambda e:ElementHandler.EXIT)
      #self.handler.add(cancelButton)
      self.nextButton = Button(self.window, y, x, "Next Page", self.proceedPage)
      self.nextButton.set_disabled_txt('You must correct the disk selection to proceed.')
      self.handler.add(self.nextButton)

  def interactive_ui(self, stdscr):
    self.stdscr = stdscr
    if self.isFirst:
      curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
      curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
      curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_RED)

      stdscr.bkgdset(' ', curses.color_pair(1))
      stdscr.clear()
      stdscr.border()

    if self.isFirst:
      self.init_ui(stdscr)
      self.isFirst = False

    self.handler.elements[0].set_focus(True)

    self.handler.process()

    if self.page < self.finalPage:
      return False
    else:
      return self.handler.lastControl == self.startButton


class Gui(object):

  def __init__(self):
    self.skip_get_params = False
    self.isFirst = True
    self.page = 0
    self.finalPage = 1

  def get_extra_params(self):
    pass

  # Prints error on the GUI
  def print_error(self, error):
    y,x = self.stdscr.getmaxyx()
    x = 20
    y = y-7
    self.window.addstr(y,x,("ERROR: %s" % error), 15)

  # Validate user inputs and prints out an error if any
  def validate_input_params(self):
    class DummyNode(object):
      def __init__(self, obj):
        self.cvm_ip = obj.svm_ip.get_displayed_text()
        self.cvm_gateway = obj.default_gw.get_displayed_text()
        self.cvm_netmask = obj.subnet_mask.get_displayed_text()
        self.hypervisor_ip = obj.host_ip.get_displayed_text()
        self.hypervisor_gateway = obj.default_gw.get_displayed_text()
        self.hypervisor_netmask = obj.subnet_mask.get_displayed_text()

    class DummyCluster(object):
      def __init__(self, obj):
        self.cvm_dns_servers = obj.dns_ip.get_displayed_text()

    class DummyConfig(object):
      def __init__(self, obj):
        self.nodes = [DummyNode(obj)]
        self.clusters = []
        if one_node_cluster:
          if obj.create_1node.selected:
            self.clusters = [DummyCluster(obj)]

    if network_setup:
      try:
        config = DummyConfig(self)
        validate_and_correct_network_addresses(config)
      except Exception as e:
        self.print_error(str(e))
        return False
    return True

  def backPage(self, ignore):
    if self.page == 1 or (self.page == 2 and network_setup):
      pass
    # add additional page logic above
    elif self.page == 0:
      return self.handler.EXIT
    else:
      ERROR("Page number %s out of range." % str(self.page))
      sys.exit(1)

    self.page = 0
    self.handler.clear()
    self.window.clear()
    self.init_ui(self.stdscr)
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def reviewPage(self, ignore):
    # It will allow when page=1 and network_setup=True
    # or page=0 and network_setup=False.
    if not self.page ^ network_setup:
      pass
    elif self.page == self.finalPage:
      return self.handler.EXIT
    else:
      ERROR("Page number %s out of range." % str(self.page))
      sys.exit(1)

    self.page += 1
    self.handler.clear()
    self.window.clear()
    self.init_review_ui()
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def init_review_ui(self):
    self.window.clear()
    self.window.border()
    self.window.keypad(1)
    y = 1
    x = 5
    breakpoint = 65
    def split_line(text, indent=0):
      lines = []
      while len(text) > breakpoint:
        point = text[0:breakpoint].rfind(" ")
        if point == -1 or text[:point+1] == " " * indent:
          lines.append(text[0:breakpoint])
          point = breakpoint - 1
        else:
          lines.append(text[0:point])
        text = " " * indent + text[point + 1:]
      else:
        lines.append(text)
      return lines

    self.window.addnstr(y, x + 25, "<< Review Actions >>", 25)
    y += 2

    action_msg = "Action: %s" % gui_message(
      self.action.get_selected_data()).replace(',', ' +')
    for i in split_line(action_msg, indent=8):
      self.window.addnstr(y, x, i, breakpoint)
      y += 1

    review_content = get_review_content(
        self.action.get_selected_data(),
        hypervisor=self.hypervisor.get_selected_data(),
        nos=self.nos.get_selected_data(),
        breakpoint=breakpoint)
    for line in review_content.split("\n"):
      for i in split_line(line, indent=3):
        self.window.addnstr(y, x, i, breakpoint)
        y += 1
    self.window.addnstr(y, x, "Are you sure you want to continue?", breakpoint)
    y += 1
    self.confirmButton = Button(self.window, y, x + 20, "Yes", self.startImaging)
    self.handler.add(self.confirmButton)

    self.backButton = Button(self.window, y, x + 10, "No", self.backPage)
    self.handler.add(self.backButton)


  def proceedPage(self, ignore):
    if self.page == 0:
      pass
    elif self.page == self.finalPage:
        return self.handler.EXIT
    else:
      ERROR("Page number %s out of range." % str(self.page))
      sys.exit(1)

    self.page += 1
    self.handler.clear()
    self.window.clear()
    self.init_network_setup_ui(self.stdscr)
    self.handler.elements[0].set_focus(True)
    return self.handler.HANDLED

  def startImaging(self, ignore):
    if self.validate_input_params():
      return self.handler.EXIT
    else:
      return self.handler.HANDLED

  def init_network_setup_ui(self, stdscr):
    self.window.clear()
    self.window.border()
    self.window.keypad(1)
    y = 1
    x = 5

    self.window.addnstr(y,x+20,"<< Nutanix Installer >>",25)
    y += 2
    gp.p_list.host_ip = ''
    gp.p_list.host_subnet_mask = ''
    gp.p_list.default_gw = ''
    gp.p_list.svm_ip = ''
    gp.p_list.svm_subnet_mask = ''
    gp.p_list.svm_default_gw = ''
    gp.p_list.cvm_vlan_id = ''
    gp.p_list.dns_ip = ''

    self.window.addstr(y,x,"NOTE: Leaving the IP information below blank will trigger",57)
    y += 1
    self.window.addstr(y,x,"the use of DHCP, which is not recommended unless the IP",55)
    y += 1
    self.window.addstr(y,x,"addresses are assigned statically in your DHCP server.",54)
    y += 2

    self.host_ip = TextEditor(self.window,y,x,
                                  "Host IP Address       :",
                                  gp.p_list.host_ip, 15)
    self.handler.add(self.host_ip)
    y += 1

    self.subnet_mask = TextEditor(self.window,y,x,
                                  "CVM/Host Subnet Mask  :",
                                  gp.p_list.host_subnet_mask, 15)
    self.handler.add(self.subnet_mask)
    y += 1

    self.default_gw = TextEditor(self.window,y,x,
                                  "CVM/Host Gateway      :",
                                  gp.p_list.default_gw, 15)
    self.handler.add(self.default_gw)
    y += 2

    self.svm_ip = TextEditor(self.window,y,x,
                                  "CVM IP Address        :",
                                  gp.p_list.svm_ip, 15)
    self.handler.add(self.svm_ip)
    y += 1

    self.cvm_vlan_id = TextEditor(self.window,y,x,
                                  "CVM Vlan ID           :",
                                  gp.p_list.cvm_vlan_id, 15)
    self.handler.add(self.cvm_vlan_id)
    y += 2

    self.dns_ip = TextEditor(self.window, y, x+35,
                                  "DNS Server:",
                                  gp.p_list.dns_ip, 15)
    self.handler.add(self.dns_ip, accepts_focus=False, visible=False)

    if one_node_cluster:
      self.create_1node = CheckBox(self.window, y, x,
                                   "Create single-node cluster?",
                                   False,
                                   disable_if_unchecked=[self.dns_ip],
                                   hide_if_unchecked=[self.dns_ip])
      self.handler.add(self.create_1node)
      y += 2

    self.backButton = Button(self.window, y, x+10, "Back", self.backPage)
    self.handler.add(self.backButton)

    self.nextButton = Button(self.window, y, x+25, "Next", self.reviewPage)
    self.handler.add(self.nextButton)

  def init_ui(self, stdscr):
    self.window.clear()
    self.window.keypad(1)

    y = 0
    x = 5

    self.window.addnstr(y, x + 20, "<< Nutanix Installer >>", 25)
    y += 2

    if(gp.block_id_detected or gp.node_position_detected or
       gp.node_serial_detected or gp.cluster_id_detected):
      self.window.addnstr(y, 2, "# Fields marked with (*) were automatically "
                                "detected.", 56)
      y += 1

    installed_hypervisor, installed_hypervisor_version = \
      sysUtil.find_hypervisor()

    state_text = ""
    if installed_hypervisor[0]:
      state_text += "# Installed Hypervisor: %s,%s" % (
        installed_hypervisor[0], installed_hypervisor_version[0])
    else:
      state_text += "# Hypervisor: NA"

    nos_on_cvm = get_nos_from_cvm()
    if nos_on_cvm:
      state_text += ", AOS: %s" % nos_on_cvm.version
    else:
      state_text += ", AOS: NA"

    self.window.addnstr(y, 2, state_text, 56)
    y += 2

    self.model = DropDown(self.window, y, x, "Node Model            : ",
                          gp.node_models, 0)
    self.handler.add(self.model)
    y += 1

    if gp.node_position_detected:
      marker = '*'
    else:
      marker = ' '

    def get_boot_devices(node_position):
      if node_position == ' ':
        return []
      # TODO: Use correct layout_type for diskless deployment.
      layout = get_layout(node_number=node_position)
      boot_devices = get_possible_boot_devices_from_layout(layout)
      if boot_devices != None:
        return [(disk.dev, disk.dev) for disk in boot_devices]
      return []

    def update_gui_with_node_position(drop_down):
      node_position = drop_down.get_selected_data()
      boot_device_choices = get_boot_devices(node_position)
      if len(boot_device_choices) >= 2:
        boot_device_choices.insert(0, ("Let phoenix decide", "NR"))
        self.boot_disk.set_choices(boot_device_choices)

    self.position = DropDown(self.window, y, x,
      "Node Position       %s : " % marker, gp.node_positions, 0,
       toggled=gp.node_position_detected,
       callback_on_change=update_gui_with_node_position)

    gp.node_position_choices = self.position
    self.handler.add(self.position)

    y += 1

    if gp.block_id_detected:
      marker = '*'
    else:
      marker = ' '
    self.block_id = TextEditor(self.window, y, x,
      "Block ID            %s :" % marker, gp.p_list.block_id, 16, upper=True)
    self.handler.add(self.block_id)
    y += 1

    if gp.node_serial_detected:
      marker = '*'
    else:
      marker = ' '
    self.node_serial = TextEditor(self.window, y, x,
      "Node Serial         %s :" % marker, gp.p_list.node_serial, 40, upper=True)
    self.handler.add(self.node_serial)
    y += 1

    if gp.cluster_id_detected:
      marker = '*'
    else:
      marker = ' '
    self.cluster_id = TextEditor(self.window, y, x,
      "Node Cluster ID     %s :" % marker, str(gp.p_list.cluster_id), 20)
    self.handler.add(self.cluster_id)
    y += 1

    if gp.p_list.svm_gb_ram is None:
      gp.p_list.svm_gb_ram = "Let phoenix decide"
    self.svm_gb_ram = TextEditor(self.window, y, x, "CVM RAM in GB [16-64] :",
                                 gp.p_list.svm_gb_ram, 20)
    self.handler.add(self.svm_gb_ram)
    y += 1

    node_position = gp.node_positions[1 - len(gp.node_positions)][1]
    boot_disk_choices = get_boot_devices(node_position)
    if len(boot_disk_choices) >= 2:
      boot_disk_choices.insert(0, ("Let phoenix decide", "NR"))
      self.boot_disk = DropDown(window=self.window, y=y, x=x,
          label="Choose Boot disk      : ", choices=boot_disk_choices, selectedIndex=0)
      self.handler.add(self.boot_disk)
      y += 1

    gui_actions = [(gui_message(action), action)
                   for action in gp.allowed_actions]

    def get_hyp_choices(action_index=0):
      hyp_images = get_hypervisor_images_for_action(
          gp.allowed_actions[action_index])
      hyp_choices = [(str(hyp), hyp) for hyp in hyp_images]
      if not hyp_choices:
        return [("Not Required", "NR")]
      return hyp_choices

    def get_nos_choices(action_index=0):
      nos_tars = get_nos_images_for_action(gp.allowed_actions[action_index])
      nos_choices = [(nos.gui_str, nos) for nos in nos_tars]
      if not nos_choices:
        return [("Not Required", "NR")]

      return nos_choices

    def update_gui_with_action(drop_down):
      index = drop_down.selectedIndex
      self.hypervisor.set_choices(get_hyp_choices(action_index=index))
      self.nos.set_choices(get_nos_choices(action_index=index))

    self.action = DropDown(window=self.window, y=y, x=x,
      label="Choose action         : ", choices=gui_actions,
      selectedIndex=0, callback_on_change=update_gui_with_action)
    self.handler.add(self.action)
    y += 2

    def get_hyperv_sku_choices(hypervisor=None):
      ret_nr = False
      if type(hypervisor) == str:
        ret_nr = ((hypervisor == "NR") or ("hyperv" not in hypervisor))
      else:
        ret_nr = ((not hypervisor) or (hypervisor.hyp_type != "hyperv"))
      if ret_nr:
        return [("Not Required", "NR")]
      hyperv_skus = [("STANDARD", "standard"), ("DATACENTER", "datacenter"),
                     ("STANDARD WITH GUI", "standard_gui"),
                     ("DATACENTER WITH GUI", "datacenter_gui")]
      return hyperv_skus

    def update_gui_with_hypervisor(drop_down):
      hypervisor = drop_down.get_selected_data()
      self.hyperv_sku.set_choices(get_hyperv_sku_choices(hypervisor))

    hyp_choices = get_hyp_choices()
    self.hypervisor = DropDown(window=self.window, y=y, x=x,
      label="Choose hypervisor     : ", choices=hyp_choices, selectedIndex=0,
      callback_on_change=update_gui_with_hypervisor)

    self.handler.add(self.hypervisor)
    y += 1

    hyperv_sku_choices = get_hyperv_sku_choices(hyp_choices[0][0])
    self.hyperv_sku = DropDown(window=self.window, y=y, x=x,
      label="Choose Hyper-V SKU    : ", choices=hyperv_sku_choices, selectedIndex=0)
    self.handler.add(self.hyperv_sku)
    y += 1

    nos_choices = get_nos_choices()
    self.nos = DropDown(window=self.window, y=y, x=x,
      label="Choose AOS            : ", choices=nos_choices, selectedIndex=0)
    self.handler.add(self.nos)

    y += 2
    x += 10
    cancelButton = Button(self.window, y, x, "Cancel",
                          lambda e: ElementHandler.EXIT)
    self.handler.add(cancelButton)

    self.nextButton = Button(self.window,y,x+10,"Next",
      self.proceedPage if network_setup else self.reviewPage)
    self.handler.add(self.nextButton)

    y += 2
    self.window.addnstr(y, x - 5, "Version: %s" % PHOENIX_VERSION,40)
    stdscr.refresh()

  def interactive_ui(self, stdscr):
    self.stdscr = stdscr

    if self.isFirst:
      curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
      curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
      curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_RED)

      stdscr.bkgdset(' ', curses.color_pair(1))
      stdscr.clear()
      stdscr.border()

      y, x = stdscr.getmaxyx()
      w = stdscr.subwin(y-2, x-2, 1, 1)
      w.bkgdset(' ', curses.color_pair(2))
      w.clear()
      w.border()
      y, x = w.getmaxyx()
      self.window = w.subwin(y-2, x-2, 2, 2)
      self.handler = ElementHandler(self.window)

    if self.isFirst:
      self.init_ui(stdscr)
      self.isFirst = False

    self.handler.elements[0].set_focus(True)
    self.handler.process()

    if self.page < self.finalPage and network_setup:
      return False
    else:
      return self.handler.lastControl == self.confirmButton


def get_node_positions():
  model = gp.p_list.model_string

  if platform.machine() == ARCH_PPC:
    return [('A ', 'A')]

  supermicro_node_positions = [('A  ','A'),('B  ','B'),('C  ','C'),('D  ','D')]
  numerical_node_positions  = [('1  ','1'),('2  ','2'),('3  ','3'),('4  ','4')]
  ucsblade_node_positions = [('A  ','A'),('B  ','B'),('C  ','C'),('D  ','D'),
                             ('E  ','E'),('F  ','F'),('G  ','G'),('H  ','H')]

  np_choices = supermicro_node_positions

  if (re.match(r'NX-[1-3].*', model)):
    np_choices = supermicro_node_positions
  elif (re.match(r'NX-[6|9].*', model)):
    supermicro_node_positions.pop()
    supermicro_node_positions.pop()
    np_choices = supermicro_node_positions
  elif (re.match(r'NX-[7|8].*', model)):
    supermicro_node_positions.pop()
    supermicro_node_positions.pop()
    supermicro_node_positions.pop()
    np_choices = supermicro_node_positions

  # Hardcode node_position to "A" for Dell and Lenovo systems excluding
  # the 2U4N systems.
  ret,out,err = shell.shell_cmd(["dmidecode -s system-manufacturer"])
  mfg = ''
  mfg_lines = out.strip().splitlines()
  if mfg_lines:
    mfg = mfg_lines[-1].lower()
  if mfg.startswith("dell") or mfg.startswith("lenovo"):
    if gp.p_list.vpd_method in ["dell_2u4n", "lenovo_2u4n"]:
      np_choices = numerical_node_positions
    else:
      np_choices = [('A  ','A')]

  # Override np_choices if quanta detected
  if re.match(r'NX-3000', model):
    np_choices = numerical_node_positions

  # UCS blades can have node positions from A through H.
  if model.startswith("Cisco UCS B"):
    np_choices = ucsblade_node_positions

  return np_choices

def deselect_checkbox(cbox_list):
  if not cbox_list:
    return True
  for cb in cbox_list:
    cb.uncheck()

def toggle_checkbox(cbox_list):
  if not cbox_list:
    return True
  for cb in cbox_list:
    cb.toggle()

def set_entity_usable(enabled, entities):
  if not entities:
    return True
  for entity in entities:
    entity.accepts_focus = enabled

def set_entity_visible(visible, entities):
  if not entities:
    return True
  for entity in entities:
    entity.visible = visible
    entity.draw()


def collect_children(basedir):
  exclusions = ('iso','svm_templates')
  choices_list = sorted(list(set(
                      [os.path.basename(h) for h in glob.glob("%s/*" % basedir)]
                      )))
  # List of tuples how UI expects it [('esx','esx'),('kvm','kvm')]
  choices = [(i.upper(), i) for i in choices_list if i not in exclusions]
  return choices


def get_params_from_gui(obj):
  # class-specific logic first
  ep = obj.get_extra_params()
  # with the option to skip the (legacy) generic stuff
  if obj.skip_get_params:
    return ep

  # Extract GUI args and put them in gp.p_list
  gp.p_list.node_position = obj.position.get_selected_data()
  gp.p_list.block_id = obj.block_id.get_displayed_text()
  gp.p_list.node_name = gp.p_list.block_id + '-' + gp.p_list.node_position
  gp.p_list.node_serial = obj.node_serial.get_displayed_text()
  gp.p_list.cluster_id = obj.cluster_id.get_displayed_text()
  svm_gb_ram = obj.svm_gb_ram.get_displayed_text()
  if svm_gb_ram == "Let phoenix decide" or type(obj) == type(CEGui()):
    gp.p_list.svm_gb_ram = None
  else:
    if((not svm_gb_ram.isdigit()) or int(svm_gb_ram) < 16 or int(svm_gb_ram) >64):
      raise Exception("CVM RAM in GB must be an integer of range 16 to 64.")
    gp.p_list.svm_gb_ram = int(svm_gb_ram)

  if hasattr(obj, 'boot_disk'):
    boot_disk = obj.boot_disk.get_selected_data()
    if boot_disk != "NR":
      boot_disk = boot_disk.replace("/dev/",'')
      disks = disk_info.collect_disk_info(disk_list_filter=[boot_disk])
      boot_disk_info = disks[boot_disk]
      gp.p_list.boot_disk_info = boot_disk_info
      gp.p_list.boot_disk = boot_disk_info.dev
      gp.p_list.boot_disk_model = boot_disk_info.model
      gp.p_list.boot_disk_sz_GB = boot_disk_info.size

  if type(obj) == type(CEGui()):
    action = obj.action
    hypervisor = obj.hypervisor
    nos = obj.nos
    error = False
    gp.p_list.ce_hyp_boot_disk = obj.hyp_boot_disk[0]
    gp.p_list.ce_cvm_boot_disks = obj.cvm_boot_disk
    gp.p_list.ce_cvm_data_disks = obj.cvm_data_disks
    disks = disk_info.collect_disk_info()
    for dev in gp.p_list.ce_cvm_boot_disks + gp.p_list.ce_cvm_data_disks:
      disk = disks[dev]
      # ESXi represents dash as 2D in disk list
      if disk.serial:
        serial = disk.serial
        serial = serial.replace("-", "2D")
        gp.p_list.ce_serials.append(serial)
        gp.p_list.ce_wwns.append(disk.wwn)

    if hypervisor.hyp_type == "esx":
      path = gp.p_list.esx_path.strip()
      ip = gp.p_list.host_ip
      subnet = gp.p_list.host_subnet_mask
      gw = gp.p_list.default_gw
      if len(path) != 0 and len(ip) != 0 and len(subnet) != 0 and len(gw) != 0:
        # calculate broadcast
        ip_parts = ip.split(".")
        sub_parts = subnet.split(".")
        broadcast = ""
        for ip_part, sub_part in zip(ip_parts, sub_parts):
          if sub_part == "255":
            broadcast += ip_part
          if sub_part == "0":
            broadcast += "255"
          if (sub_part != "255" and sub_part != "0"):
            multiplier = 256 - int(sub_part)
            result = 0
            while result <= int(ip_part):
              result += multiplier
            result = result - 1
            broadcast += str(result)
          broadcast += "."
        broadcast = broadcast[:-1]
        # create folder
        shell.shell_cmd(['mkdir /esx_tmp'], fatal=False, ttyout=True)
        shell.shell_cmd(['chmod 666 /esx_tmp'], fatal=False, ttyout=True)
        # create network
        print ("Configuring network...")
        shell.shell_cmd(['ifconfig eth0 ' + ip + ' broadcast ' + broadcast + ' netmask ' + subnet + ' up'], fatal=False,
                        ttyout=True)
        shell.shell_cmd(['route add default gw ' + gw], fatal=False, ttyout=True)
        # copy from path
        time.sleep(5)
        print("Copying ISO image...")
        shell.shell_cmd(['cp /mnt/iso/images/hypervisor/esx/* /esx_tmp'], fatal=False, ttyout=True)
        # add path to esx iso
        ret, out, err = shell.shell_cmd(['ls /esx_tmp'])
        lines = out.split('\n')
        for line in lines:
          if "iso" in line:
            hypervisor.path = "/esx_tmp/" + line
          else:
            ERROR("ISO was not successfully downloaded, make sure URL: " + path +" you provided is accessible from: " + ip)
            error = True
      else:
        ERROR ("All host network information and URL to ESXi ISO must be given for ESXi hypervisor installation.")
        error = True
    if error:
      raise ValidationError()
  else:
    action = obj.action.get_selected_data()
    hypervisor = obj.hypervisor.get_selected_data()
    nos = obj.nos.get_selected_data()
    hyperv_sku = obj.hyperv_sku.get_selected_data()

  if hypervisor == "NR":
    (_, hyp_type), (_, hyp_version) = sysUtil.find_hypervisor()
    gp.p_list.hyp_type = hyp_type
    gp.p_list.hypervisor_iso_path = None
    gp.p_list.hyp_version = hyp_version
  else:
    gp.p_list.hyp_type = hypervisor.hyp_type
    gp.p_list.hypervisor_iso_path = hypervisor.path

  if ((gp.p_list.hyp_type == "hyperv") and (hyperv_sku != "NR")):
    gp.p_list.hyperv_sku = hyperv_sku

  if nos == "NR":
    gp.p_list.nos_version = None
    gp.p_list.installer_path = None
  else:
    gp.p_list.svm_version = gp.p_list.nos_version = nos.version
    gp.p_list.installer_path = nos.path

  if (action == INSTALL_HYPERVISOR or INSTALL_HYPERVISOR in action or
          action == CONFIGURE_HYPERVISOR or CONFIGURE_HYPERVISOR in action):
    gp.p_list.hyp_install_type = "clean"
  else:
    gp.p_list.hyp_install_type = None

  if network_setup:
    gp.p_list.host_ip = obj.host_ip.get_displayed_text()
    gp.p_list.host_subnet_mask = obj.subnet_mask.get_displayed_text()
    gp.p_list.default_gw = obj.default_gw.get_displayed_text()
    gp.p_list.svm_ip = obj.svm_ip.get_displayed_text()
    gp.p_list.svm_subnet_mask = obj.subnet_mask.get_displayed_text()
    gp.p_list.svm_default_gw = obj.default_gw.get_displayed_text()
    gp.p_list.cvm_vlan_id = obj.cvm_vlan_id.get_displayed_text()
    if gp.p_list.cvm_vlan_id == '':
      gp.p_list.cvm_vlan_id = None
    gp.p_list.dns_ip = ''
    if one_node_cluster:
      gp.p_list.create_1node_cluster = obj.create_1node.selected
      if obj.create_1node.selected:
        gp.p_list.dns_ip = obj.dns_ip.get_displayed_text()

  if action == INSTALL_SVM or INSTALL_SVM in action:
    gp.p_list.svm_install_type = "clean"
  elif action == REPAIR_SVM or REPAIR_SVM in action:
    gp.p_list.svm_install_type = "repair"
  else:
    gp.p_list.svm_install_type = None

  driver_package = os.path.join(IMAGES_DIR, DRIVER_PACKAGE_NAME)
  if os.path.exists(driver_package):
    # Copy it out of the CD image.
    if not os.path.exists(DRIVERS_DIR):
      os.mkdir(DRIVERS_DIR)
    shutil.copy(driver_package, DRIVERS_DIR)
    gp.p_list.driver_package = os.path.join(DRIVERS_DIR, DRIVER_PACKAGE_NAME)

  if gp.p_list.hyp_type == "esx":
    if not gp.p_list.hypervisor_iso_path:
      gp.p_list.hyp_version, gp.p_list.bootbank = \
                                               sysUtil.get_esx_info(gp.p_list)

  return gp.p_list

def get_params(guitype, _one_node_cluster=False, _network_setup=False):
  """
  Run the GUI, and return param_list.
  """
  global one_node_cluster, network_setup
  one_node_cluster = _one_node_cluster
  network_setup = _network_setup

  if guitype().skip_get_params:
    return run_gui(guitype)

  # Add check to look for the default Dell error file. If present then treat
  # this session like a Dell factory imaging session.
  if os.path.exists(os.path.join(factory_exchange_dir(), "FIST.err")):
    gp.p_list.factory_error_flag_file = "FIST.err"
    gp.p_list.factory_logfile_info = "phoenix_info.txt"
    gp.p_list.factory_logfile_error = "phoenix_error.txt"
    set_log_fatal_callback(fatal_exc_handler, (3,))

  # Find existing nutanix partition and load existing factory config
  # if it exists on that partition
  gp.factory_config = sysUtil.find_factory_config()

  # Auto-detect model and other parameters.
  sysUtil.detect_params(gp.p_list, throw_on_fatal=False, skip_esx_info=True)

  # Display a banner if this is a Dell 2U4N (XC6320) system informing the user
  # that it is absolutely critical to get node position and Block ID correct.
  if (gp.p_list.vpd_method == "dell_2u4n" and
      gp.p_list.model_string.startswith("XC6320")):
    answer = None
    while answer != 'Y':
      shell.shell_cmd(['clear'], fatal=False, ttyout=True)
      sys.stdout.write(XC6320_BANNER)
      answer = input("\nPlease enter 'Y' to proceed to the UI: ").upper()

  if not gp.factory_config:
    gp.svm_data = sysUtil.find_svm_data()

  # Find detected elements and ensure they are indicated in the UI as
  # having been detected automatically from the hardware.
  if gp.p_list.block_id:
    gp.block_id_detected = True

  if gp.p_list.node_serial:
    try:
      UUID(gp.p_list.node_serial)
    except ValueError:
      gp.node_serial_detected = True

  if gp.p_list.node_position:
    # Detected node position from BMC or backplane.
    gp.node_position_detected = True
    gp.node_positions = [('%s  ' % gp.p_list.node_position,
                                   gp.p_list.node_position)]
  else:
    gp.node_positions = get_node_positions()
    if len(gp.node_positions) == 1:
      gp.node_position_detected = True
    else:
      # Insert an empty entry for node position to ensure that the user actually
      # selects something manually.  There have been too many instances where
      # users think that Phoenix has populated that information for them only
      # to realize later that they imaged all nodes in the block with node
      # position 'A'
      gp.node_positions.insert(0, ('   ', ' '))

  if gp.p_list.cluster_id:
    gp.cluster_id_detected = True

  gp.node_models = [(gp.p_list.model_string, gp.p_list.model_string)]

  gp.allowed_actions = determine_actions()
  return run_gui(guitype)


def run_gui(guitype):
  gui = guitype()
  if type(gui) == type(CEGui()):
    global ce_gui
    ce_gui = gui

  disable_ttyout_handler()
  while True:
    try:
      isSave = curses.wrapper(gui.interactive_ui)
      enable_ttyout_handler()
    except curses.error as e:
      if e.args[0].count("str() returned ERR") > 0:
        print("Terminal screen is not large enough to run the installation " \
              "script. Please resize the terminal and rerun the script.")
        sys.exit(1)
      raise e
    if not isSave:
      sys.exit(2)
    try:
      shell.shell_cmd(['clear'], fatal=False, ttyout=True)
      params = get_params_from_gui(gui)
      params.validate()
      return params
    except ValidationError as e:
      sys.stdout.write(str(e))
      sys.stdout.write("\n")
      input("Press 'enter' to continue")

__all__ = ["get_params"]
