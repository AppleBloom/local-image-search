#!/usr/bin/python2
# - coding: utf-8 -
''' my way of handling Tracker (via dbus) '''

# imports:
import dbus



def sanitize_string (s):
		''' ... to use as a literal in a sparql query '''
		return s.replace ('\\', '\\\\').replace ("'", "\\'")


class TrackerClient():
	''' a wrapper for Tracker client dbus interface 
	with search and tag-handling extensions '''
	
	def __init__(self):
		
		bus = dbus.SessionBus()
		obj = bus.get_object('org.freedesktop.Tracker1',
			'/org/freedesktop/Tracker1/Resources')
		self.tracker = dbus.Interface(obj, 'org.freedesktop.Tracker1.Resources')
	
	def query (self, query):
		ans = self.tracker.SparqlQuery (query)
		return [[val.encode('utf-8') for val in rec] for rec in ans]
	
	
	# Tag operations vvvvvvvvvvvvvvvvvvvvvvvvvvvvv
	
	
	def tag_list (self, file_uri=None, limit=None):
		
		query = '''SELECT ?labels
				WHERE {
				?f nao:hasTag ?tags .
				'''+ ('''?f nie:url '%s' .
				'''% file_uri if file_uri else "")+'''
				?tags a nao:Tag ;
					nao:prefLabel ?labels .
				} ORDER BY ASC(?labels)'''\
				+ (" LIMIT %d"%limit if  limit is not None else "")
		ans = self.tracker.SparqlQuery (query)
		tags = [s[0].encode('utf-8') for s in ans]

		if file_uri is not None:
			return tags
		else:
			tagsN = []
			previous = None
			for tag in tags:
				if tag!=previous:
					tagsN.append ([tag, 1])
				else:
					tagsN [-1][1] += 1
				previous = tag
			return sorted (tagsN, key=lambda e: e[1], reverse=True)


	def how_many (self, tag):
		''' ... resources with the given tag '''
		
		query = '''SELECT COUNT(?f)
				WHERE {
				?f nao:hasTag ?t .
				?t nao:prefLabel '%s' ;
					a nao:Tag .}''' % sanitize_string (tag)
		ans = self.tracker.SparqlQuery (query)
		return int (ans[0][0])


	def add_tag (self, tag, file_uri, first = False):
		''' If the tag doesn't exist in the database, (first) should be True. '''
		
		if first:
			query = '''INSERT {
						_:tag a nao:Tag ;
							nao:prefLabel '%s' .
						?f nao:hasTag _:tag .
					} WHERE {
						?f nie:url '%s' .}''' % (sanitize_string (tag), file_uri)
		else:
			query = '''INSERT {
						?f nao:hasTag ?t .
					} WHERE {
						?t nao:prefLabel '%s' ;
							a nao:Tag .
						?f nie:url '%s' .}''' % (sanitize_string (tag), file_uri)
		self.tracker.SparqlUpdate (query)


	def del_tag (self, tag, file_uri = None):
		''' If no (file_uri) is given, every triple containing the (tag) will be removed. '''
		
		if file_uri is None:
			get_id = '''SELECT ?t 
					WHERE {
						?t a nao:Tag ;
							nao:prefLabel '%s' .}''' % sanitize_string (tag)
			ids = [s[0].encode('utf-8') for s in self.tracker.SparqlQuery (get_id)]
			for id in ids:
				self.del_res (id)
		else:
			query = '''DELETE {
						?f nao:hasTag ?t .
					} WHERE {
						?t nao:prefLabel '%s' ;
							a nao:Tag .
						?f nie:url '%s' .}''' % (sanitize_string (tag), file_uri)
			self.tracker.SparqlUpdate (query)
	
	
	def res_by_tag (self, tag, type=None):
		''' (type) is a class of resources to look at. '''
		
		query = '''SELECT ?id
				WHERE {
					?id nao:hasTag ?t .
					'''+("" if type is None else ("?id a %s ." % type)) +'''
					?t a nao:Tag ; nao:prefLabel '%s' .}''' % (sanitize_string (tag))
		ans = self.tracker.SparqlQuery (query)
		return [res[0].encode('utf-8') for res in ans]
	
	
	# Tag operations ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
	
	
	def all_out (self, uri):
		''' dictionary of all the triples with (uri) as subject '''
		
		query = '''SELECT ?p ?x
				WHERE {
					<%s> ?p ?x .}''' % uri
		ans = self.tracker.SparqlQuery (query)
		return dict (((a[0].encode('utf-8'), a[1].encode('utf-8')) for a in ans))
	
	def all_in (self, uri):
		''' dictionary of all the triples with (uri) as object '''
		
		query = '''SELECT ?p ?x
				WHERE {
					?x ?p <%s> .}''' % uri
		ans = self.tracker.SparqlQuery (query)
		return dict (((a[0].encode('utf-8'), a[1].encode('utf-8')) for a in ans))
	
	def del_res (self, uri):
		''' delete all triples containing the resource with a given uri '''
		
		query1 = '''DELETE {
					?x ?p <%s> .
				} WHERE {
					?x ?p <%s> .}''' % (uri, uri)
		query2 = '''DELETE {
					<%s> ?p ?x .
				} WHERE {
					<%s> ?p ?x .}''' % (uri, uri)
		self.tracker.SparqlUpdate (query1)
		self.tracker.SparqlUpdate (query2)
	
	def res_by_url (self, url):
		query = "SELECT ?f { ?f nie:url '%s' }" % sanitize_string (url)
		ans = self.tracker.SparqlQuery (query)
		return [rec[0].encode('utf-8') for rec in ans]
	
	def get_props (self, uris, props, opt_props=[]):
		''' (props) and (opt_props) are names of properties to retrieve
		from objects listed by (uris). Absence of (opt_props) properties
		doesn't make the request invalid (for them empty strings are returned).
		
		(uris) and (props+opt_props) should be nonempty lists. '''
		
		all_props = props + opt_props
		vars = ['?p'+str (n) for n in xrange (len (props))]
		opt_vars = ['?op'+str (n) for n in xrange (len (opt_props))]
		condition = ""
		
		# properties block:
		for i, prop in enumerate (props):
			condition+= "?f %s %s .\n" % (prop, vars[i])
		
		# optional properties block:
		for i, prop in enumerate (opt_props):
			condition+= "OPTIONAL {?f %s %s .}\n" % (prop, opt_vars[i])
		
		# uris filter:
		condition+= "FILTER (%s)" % ' || '.join ((("?f = <%s>" % uri) for uri in uris))
			
		# request:
		query = "SELECT %s WHERE {\n%s}" % (' '.join (vars+opt_vars), condition)
		ans = self.tracker.SparqlQuery (query)
		return [dict (((all_props[i], a[i].encode('utf-8')) for i in xrange (len (all_props)))) for a in ans]
	
	
	def res_by_exp (self, preds, exp, restrictions=""):
		'''
		(preds) -- list of (Condition) instances;
		(exp) -- logical function (sparql filter style) with placeholders (str.format style), each of which corresponds to a predicate from (preds).
		'''
		
		# useful patterns:
		#~ import re
		#~ strexp = re.compile ("(?<!%)((%%)*)%s")
		vars = ['?v'+str(i) for i in xrange (len (preds))]
		
		# optional block:
		optional = ""
		for i in xrange (len (preds)):
			prefix = vars [i] + '_'
			locals = [vars [i]] +\
				[prefix+str(j) for j in xrange (preds [i] .var_num)]
			optional+= "\nOPTIONAL {%s}" % (preds [i] .txt .format (*locals))
		
		# request:
		query = '''SELECT ?x
			WHERE {
				%s%s
				FILTER (%s)
			}''' % (restrictions, optional,\
				exp.format (*( " bound(%s) "%var for var in vars )))
		ans = self.tracker.SparqlQuery (query)
		return [res[0].encode('utf-8') for res in ans] #TODO:    order? duplicates?


# conditions for  TrackerClient.res_by_exp:

class Condition():
	''' sparql query condition string '''
	
	def __init__(self, txt, var_num):
		self.txt = txt
		self.var_num = var_num


class ConditionTemplate():
	'''  '''
	
	def __init__(self, txt, var_num, sub_num):
		self.txt, self.var_num, self.sub_num  =  txt, var_num, sub_num
	
	
	def sub (self, subs):
		
		if type (subs) in (str, unicode):
			subs = [subs]
		txt, var_num, sub_num = self.txt, self.var_num, self.sub_num
		
		if len (subs) != sub_num:
			raise Exception ('this template has exactly %d parameter(s)' % sub_num)
		subs = [sanitize_string (sub) for sub in subs]
		return Condition (txt.format (*subs), var_num)


basicConditions = {\
	'hasTag': ConditionTemplate ('''?x nao:hasTag {{0}} .
					{{0}} a nao:Tag; nao:prefLabel '{0}' .''', 1, 1),\
	'hasTag_i': ConditionTemplate ('''?x nao:hasTag {{0}} .
					{{0}} a nao:Tag; nao:prefLabel {{1}} .
					FILTER regex({{1}}, '^{0}$', 'i') ''', 2, 1),\
	'fts_seq': ConditionTemplate ('''{{{{ ?x fts:match '"{0}"' }}}} UNION {{{{ ?x nao:hasTag {{1}} .
					{{1}} a nao:Tag; fts:match '"{0}"' }}}}
					rdfs:Class rdfs:subClassOf {{0}} .''', 2, 1), # well, the last line is a hack: \
					# we need a variable that is bound iff the condition is true \
	'inURL': ConditionTemplate ('''?x nie:url {{0}} .
					FILTER regex({{0}}, '{0}', 'i') ''', 1, 1),\
	'inClass': ConditionTemplate ('''?x a {0} .
					rdfs:Class rdfs:subClassOf {{0}} .''', 1, 1)\
}
