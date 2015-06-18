#!/usr/bin/python2
# - coding: utf-8 -

# basic imports:
import pygtk
pygtk.require("2.0")
import gtk
import gobject

import hashlib
from urllib import url2pathname, quote

# environment:
import sys
import os.path
script_dir = os.path.dirname (os.path.abspath (__file__))
sys.path.append (script_dir+'/..')
stderr = sys.stderr
clipboard = gtk.clipboard_get()

main_iface = script_dir +"/i-face/main.glade"
placeholder_icon = script_dir +"/i-face/no_thumbnail.png"
thumbnail_dir = os.path.expanduser ("~/.thumbnails/normal/")

# my modules:
import tracker_via_dbus as tracker



# useful stuff:
def md5 (s):
	m = hashlib.md5()
	m.update (s)
	return m.hexdigest()


def create_thumbnail (path):
	
	pixbuf = gtk.gdk.pixbuf_new_from_file (path)
	w, h = pixbuf.get_width(), pixbuf.get_height()
	if max (w,h)<=128:
		return pixbuf
	scale = 128. / max (w,h)
	nw, nh = int (w*scale), int (h*scale)
	return pixbuf.scale_simple (nw, nh, gtk.gdk.INTERP_BILINEAR)


def unique (iterable):
	return list (set (iterable))



# the main class
class Searcher():
	''' applet '''
	
	def __init__(self, gladefile, db):
		
		# init glade:
		self.builder = gtk.Builder() 
		self.builder.add_from_file (gladefile)
		
		# useful objects:
		self.db = db
		self.store = self.builder.get_object ('image_store')
		self.view = self.builder.get_object ('image_view')
		sel_action_store = self.builder.get_object ('sel_action_store')
		self.sel_action_box = self.builder.get_object ('sel_action_box')
		act_action_store = self.builder.get_object ('act_action_store')
		self.act_action_box = self.builder.get_object ('act_action_box')
		self.entry = self.builder.get_object ('query_entry')
		self.status = self.builder.get_object ('statusbar')
		self.word_store = self.builder.get_object ('word_store')
		self.DBusException = tracker.dbus.exceptions.DBusException
		
		# status contexts:
		self.page_context = self.status.get_context_id ('page context')
		self.action_context = self.status.get_context_id ('action context')
		
		# page mechanics:
		self.item_ids = []
		self.page_capacity = 10
		self.current_page = 0
		self.page_ceil = 1
		
		# syntax:
		self.tag_condition = tracker.basicConditions ['hasTag_i']
		self.url_condition = tracker.basicConditions ['inURL']
		self.fts_condition = tracker.basicConditions ['fts_seq']
		self.space = u' 	'
		self.in_syntax = u'()|&+-!'
		self.out_syntax = ['(',')','||','&&','&&','&& !','!' ]
		self.predicates = u'.:'
		self.delimiters = '"' +self.space +self.predicates +self.in_syntax
		self.delimiters_utf = self.delimiters.encode ('utf-8')
		self.unary = u'!'
		self.binary = u'|&+'
		self.res_class = 'nfo:Image'
		self.aliases = {'image':'nfo:Image', 'video':'nfo:Video',
					'doc':'nfo:Document', 'document':'nfo:Document',
					'file': 'nfo:FileDataObject'}
		
		# auto-completion:
		for word in [')class=', ')capacity=']:
			self.word_store.append ([word])
		for item in db.tag_list (limit=1000):
			self.word_store.append ([item [0]
				.replace ('\\', '\\\\') .replace ('"', '\\"') .lower()])
		completion = gtk.EntryCompletion()
		completion.set_model (self.word_store)
		completion.set_text_column (0)
		completion.set_match_func (self._complete_match_func)
		completion.connect ('match-selected', self._on_complete)
		self.entry.set_completion (completion)
		
		# connect signals:
		signals = {
				'on_return' : self._on_return,
				'on_select': self._on_select,
				'on_poke': self._on_poke,
				'on_sel_action_changed': self. _on_sel_changed,
				'on_act_action_changed': self. _on_act_changed,
				'on_move_left': self. _on_left,
				'on_move_right': self._on_right,
				'on_destroy' : self._on_destroy}
		self.builder.connect_signals (signals)
		
		# add actions:
		self.sel_action = None
		self.act_action = None
		self.sel_action_map = [('copy path', self.copy_path),
					('copy URL', self.copy_url),
					('--', None)]
		self.act_action_map = [('ImageMagick', self.display_im),
					('xdg default', self.display_def),
					('open folder', self.display_dir),
					('--', None)]
		for a in self.sel_action_map:
			sel_action_store.append ((a[0],))
		for a in self.act_action_map:
			act_action_store.append ((a[0],))
		self.sel_action_box.set_active (0)
		self.act_action_box.set_active (0)
	
	
	# Actions vvvvvvvvvvvvvvvvvvvvvvvvvv
	
	def copy_path (self, ix):
		txt = self.store [ix][4]
		clipboard.set_text (txt)
		self.status.pop (self.action_context)
		self.status.push (self.action_context, "Clipboard: "+txt)
	
	def copy_url (self, ix):
		txt = self.store [ix][3]
		clipboard.set_text (txt)
		self.status.pop (self.action_context)
		self.status.push (self.action_context, "Clipboard: "+txt)
	
	def display_im (self, path):
		import subprocess
		subprocess.Popen (['display', '-title', path, path])
	
	def display_def (self, path):
		import subprocess
		subprocess.Popen (['xdg-open', path])
	
	def display_dir (self, path):
		import subprocess
		subprocess.Popen (['xdg-open', os.path.dirname (path)])
	
	# Actions ^^^^^^^^^^^^^^^^^^^^^^^^^^
	
	
	# Processing search queries vvvvvvvvvvvvvvvvvvvvvvvvvv
	
	class ParsingException(Exception):
		pass
	
	def lexer (self, query):
		''' breaks (query) into list of tokens, i.e. (type, str)-tuples '''
		
		space = self.space
		syntax = self.in_syntax
		predicates = self.predicates
		delimiters = self.delimiters
		query = query.decode ('utf-8') + u' '
		
		state_stack = ['syntax']
		token_stack = []
		predicate = u''
		tag_stack = u''
		
		i = 0
		while i < len (query):
			c = query [i]
			
			if state_stack[-1] =='syntax':
				if c in space:
					pass
				elif c in syntax:
					token_stack.append ((u'syn', c))
				elif c in predicates:
					predicate = c
					state_stack.append ('prefix')
				elif c=='"':
					state_stack.append ('"tag"')
				else:
					state_stack.append ('tag')
					tag_stack+= c
			
			elif state_stack[-1] =='prefix':
				if c=='"':
					state_stack.pop()
					state_stack.append ('"tag"')
				elif c in delimiters:
					raise Searcher.ParsingException (u'no tag after a predicate '+predicate)
				else:
					state_stack.pop()
					state_stack.append ('tag')
					tag_stack+= c
				
			elif state_stack[-1] =='tag':
				if c in delimiters:
					token_stack.append ((predicate+u'tag', tag_stack))
					tag_stack = u''
					predicate = u''
					state_stack.pop()
					continue # that's why we can't has for-loops
				else:
					tag_stack+= c
			
			elif state_stack[-1] =='"tag"':
				if c==u'\\':
					state_stack.append ('esc')
				elif c==u'"':
					token_stack.append ((predicate+u'tag', tag_stack))
					tag_stack = u''
					predicate = u''
					state_stack.pop()
				else:
					tag_stack+= c
			
			elif state_stack[-1] =='esc':
				if c in u'\\"':
					tag_stack+= c
					state_stack.pop()
				else:
					raise Searcher.ParsingException (u'invalid escape sequence \\'+c)
			i+= 1
		
		if state_stack[-1] !='syntax':
			raise Searcher.ParsingException (u'syntactic error')
		return token_stack
	
	
	def translator (self, tokens):
		''' translates (tokens) into a form suitable for tracker_via_dbus.res_by_exp '''
		
		# token subtypes:
		unary, binary = self.unary, self.binary
		operators = unary+binary
		right_side_syn = u'('+unary
		
		def right_side (token):
			return token[0]!=u'syn' or token[1] in right_side_syn
		def left_side (token):
			return token[0]!=u'syn' or token[1] ==u')'
		lbrace = (u'syn', u'(')
		rbrace = (u'syn', u')')
		
		# check and transform:
		if len (tokens)==0:
			raise Searcher.ParsingException ('empty query')
		
		prev = tokens [0]# token before the current
		depth = 0# bracket levels
		if prev == lbrace:
			depth = 1
		elif prev == rbrace:
			raise Searcher.ParsingException ('missing brace')
		i = 1
		
		while i < len (tokens):
			next = tokens [i]# current token
			if left_side (prev) and right_side (next):
				next = (u'syn', '|')
				tokens.insert (i, next)
			else:
				if not right_side (next) and prev[0]==u'syn' and prev[1] in operators:
					raise Searcher.ParsingException ('invalid syntax: '+prev[1]+next[1])
				if not left_side (prev) and next[0]==u'syn' and next[1] in binary:
					raise Searcher.ParsingException ('invalid syntax: '+prev[1]+next[1])
				if prev == lbrace and next == rbrace:
					raise Searcher.ParsingException ('empty braces')
			
			if next == lbrace:
				depth+= 1
			elif next == rbrace:
				depth-= 1
			if depth < 0:
				raise Searcher.ParsingException ('missing brace')
			
			prev = next
			i+= 1
		
		if depth!= 0:
			raise Searcher.ParsingException ('missing brace')
		
		# translate:
		syntax_equiv = {}
		for i in xrange (len (self.in_syntax)):
			syntax_equiv [self.in_syntax [i]] = self.out_syntax [i]
		
		conditions = []
		expression = ''
		for token in tokens:
			if token [0]==u'syn':
				expression+= syntax_equiv [token [1]]
			else:
				expression+= "{%s}" % str (len (conditions))
			
			if token [0]==u'tag':
				conditions.append (self.tag_condition.sub (token[1].encode ('utf-8')))
			elif token [0]==u'.tag':
				conditions.append (self.url_condition.sub (quote (token[1].encode ('utf-8'))))
			elif token [0]==u':tag':
				conditions.append (self.fts_condition.sub (token[1].encode ('utf-8')))
		
		return conditions, expression
	
	
	def interpreter (self, query0):
		''' deal with the user's query '''
		
		# settings:
		if query0.startswith (')'):
			try:
				var, val = query0 [1:].split ('=')
				var, val = var.strip(), val.strip()
			except:
				return [], 'Unrecognized command', False
				
			if var == 'class':
				if val in self.aliases:
					val = self.aliases [val]
				import re
				if not re.match('(\\w+:\\w+)|(<[^\\\\>]+>)$', val):
					return [], 'Bad class: '+val, False
				# set:
				self.res_class = val
				return [], 'Looking for: '+val, False
			
			elif var == 'capacity':
				try:
					val = int (val)
					if not (0<val<1000):
						raise Exception()
				except:
					return [], 'Bad integer: '+str(val), False
				# set:
				self.page_capacity = val
				return self.item_ids, 'Page capacity set to: '+str(val), True
			
			else:
				return [], 'Unrecognized parameter', False
		
		# search queries:
		try:
			tokens = self.lexer (query0)
			conditions, query = self.translator (tokens)
		except Searcher.ParsingException as e:
			return [], 'Parsing: ' + str(e), True
		
		try:
			restriction = "?x a %s ." % self.res_class
			return unique (self.db.res_by_exp (conditions, query, restriction)), "",  True
		except self.DBusException as e:
			print>>stderr, "Tracker:", e
			return [], "Tracker didn't accept the request", True
	
	
	#Processing search queries ^^^^^^^^^^^^^^^^^^^^^^^^^^
	
	
	def refresh (self):
		''' fills the IconView with relevant items '''
		
		pageN = self.current_page
		step = self.page_capacity
		files = self.item_ids [pageN*step : (pageN+1)*step]
		self.store.clear()
		if len (files)==0:
			return
		
		# populate the page:
		retrieved = 0
		file_info = self.db.get_props (files, ['nie:url', 'nfo:fileName', 'nie:mimeType'],
								['nfo:height' , 'nfo:width'])
		for item in file_info:
			
			# get useful strings:
			if item ['nie:url'][0:8] != "file:///":
				continue
			w = item ['nfo:width'] if item ['nfo:width'] != '' else '?'
			h = item ['nfo:height'] if item ['nfo:height'] != '' else '?'
			label = "%s x %s\n<i>%s</i>" % (w, h, item ['nie:mimeType'])
			path = url2pathname (item ['nie:url']) [7:]
			thumb_path = thumbnail_dir + md5 (item ['nie:url']) + '.png'
			
			# get thumbnail
			try:
				pixbuf = gtk.gdk.pixbuf_new_from_file (thumb_path)
			except gobject.GError as e:
				try:
					pixbuf = create_thumbnail (path)
					label = "<span color='#080'>"+label+"</span>"
				except gobject.GError as e:
					pixbuf = gtk.gdk.pixbuf_new_from_file (placeholder_icon)
					label = "<span color='#800'>"+label+"</span>"
			
			# put it all together:
			self.store.append ((pixbuf, label, path, item['nie:url'], path))
			retrieved+= 1
		
		# update status:
		self.status.pop (self.page_context)
		msg = "%d result(s). Page %d of %d."\
			% (len (self.item_ids), pageN+1, self.page_ceil)
		if retrieved != len (files):
			msg+= " Unable to show %d object(s)." % (len (files) - retrieved)
		self.status.push (self.page_context, msg)
	
	
	# Auto-completion vvvvvvvvvvvvvvvvvvvvvvvvvv
	
	def last_word (self, txt): #TODO:    more accurate algorithm
		''' last part of the (self.entry) text content
		that is likely to be a part of a single tag name '''
		
		pos = self.entry.get_position()
		
		if txt.startswith (')'):
			return txt [: pos]
		
		i = None
		for i in xrange (pos -1, -1, -1):
			if txt[i] in self.delimiters_utf:
				i+= 1
				break
		return txt [i : pos]
	
	
	def _complete_match_func (self, completion, key, iter):
		key = self.last_word (key)
		option = self.word_store.get_value (iter, 0)
		return (3<= len (key)< len (option)) and option.startswith (key)
	
	def _on_complete (self, completion, model, iter):
		txt = self.entry.get_text()
		key = self.last_word (txt)
		pos = self.entry.get_position()
		prefix =  txt [0 : pos - len (key)]
		suffix = txt [pos :]
		new_txt = prefix + model.get_value (iter, 0) + suffix
		self.entry.set_text (new_txt)
		self.entry.set_position (len (new_txt) - len (suffix))
		return True
	
	# Auto-completion ^^^^^^^^^^^^^^^^^^^^^^^^^^
	
	
	def _on_return (self, widget):
		''' when you hit Enter in the entry field '''
		
		# request:
		query = self.entry.get_text() .strip()
		found, msg, refresh = self.interpreter (query)
		if len (found) == 0:
			if refresh:
				self.status.pop (self.page_context)
				self.status.push (self.page_context, "0 results. "+msg)
			else:
				self.status.pop (self.action_context)
				self.status.push (self.action_context, msg)
		
		if refresh:
			# page mechanics:
			self.current_page = 0
			self.page_ceil = len (found) / self.page_capacity
			if len (found) % self.page_capacity != 0:
				self.page_ceil+= 1
			self.item_ids = found
			self.refresh()
	
	
	def _on_select (self, widget):
		self.status.pop (self.action_context)
		selection = self.view.get_selected_items()
		if len (selection)==1 and self.sel_action is not None:
			self.sel_action (selection[0][0])
		elif len (selection)>1:
			print>> stderr, 'Awkward: more than one item is selected!'
	
	def _on_poke (self, widget, path):
		file_path = self.store [path [0]] [4]
		if self.act_action is not None:
			self.act_action (file_path)
	
	def _on_sel_changed (self, widget):
		self.sel_action = self.sel_action_map [self.sel_action_box.get_active()] [1]
		self._on_select (None)
	
	def _on_act_changed (self, widget):
		self.act_action = self.act_action_map [self.act_action_box.get_active()] [1]
	
	def _on_left (self, button):
		self.current_page = max (self.current_page-1, 0)
		self.refresh()
	
	def _on_right (self, button):
		self.current_page = min (self.current_page+1, self.page_ceil-1)
		self.refresh()
	
	def _on_destroy (self, widget):
		clipboard.store()
		gtk.main_quit()



if __name__ == "__main__":
	
	trackie = tracker.TrackerClient()
	searchy = Searcher (main_iface, trackie)
	
	gtk.main()