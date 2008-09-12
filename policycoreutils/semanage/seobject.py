#! /usr/bin/python -E
# Copyright (C) 2005, 2006, 2007, 2008 Red Hat 
# see file 'COPYING' for use and warranty information
#
# semanage is a tool for managing SELinux configuration files
#
#    This program is free software; you can redistribute it and/or
#    modify it under the terms of the GNU General Public License as
#    published by the Free Software Foundation; either version 2 of
#    the License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA     
#                                        02111-1307  USA
#
#  

import pwd, grp, string, selinux, tempfile, os, re, sys
from semanage import *;
PROGNAME="policycoreutils"
import sepolgen.module as module

import gettext
gettext.bindtextdomain(PROGNAME, "/usr/share/locale")
gettext.textdomain(PROGNAME)
try:
       gettext.install(PROGNAME, localedir="/usr/share/locale", unicode=1)
except IOError:
       import __builtin__
       __builtin__.__dict__['_'] = unicode

is_mls_enabled = selinux.is_selinux_mls_enabled()

import syslog

handle = None

def get_handle(store):
       global handle

       handle = semanage_handle_create()
       if not handle:
              raise ValueError(_("Could not create semanage handle"))
       
       if store != "":
              semanage_select_store(handle, store, SEMANAGE_CON_DIRECT);

       if not semanage_is_managed(handle):
              semanage_handle_destroy(handle)
              raise ValueError(_("SELinux policy is not managed or store cannot be accessed."))

       rc = semanage_access_check(handle)
       if rc < SEMANAGE_CAN_READ:
              semanage_handle_destroy(handle)
              raise ValueError(_("Cannot read policy store."))

       rc = semanage_connect(handle)
       if rc < 0:
              semanage_handle_destroy(handle)
              raise ValueError(_("Could not establish semanage connection"))       
       return handle

file_types = {}
file_types[""] = SEMANAGE_FCONTEXT_ALL;
file_types["all files"] = SEMANAGE_FCONTEXT_ALL;
file_types["--"] = SEMANAGE_FCONTEXT_REG;
file_types["regular file"] = SEMANAGE_FCONTEXT_REG;
file_types["-d"] = SEMANAGE_FCONTEXT_DIR;
file_types["directory"] = SEMANAGE_FCONTEXT_DIR;
file_types["-c"] = SEMANAGE_FCONTEXT_CHAR;
file_types["character device"] = SEMANAGE_FCONTEXT_CHAR;
file_types["-b"] = SEMANAGE_FCONTEXT_BLOCK;
file_types["block device"] = SEMANAGE_FCONTEXT_BLOCK;
file_types["-s"] = SEMANAGE_FCONTEXT_SOCK;
file_types["socket"] = SEMANAGE_FCONTEXT_SOCK;
file_types["-l"] = SEMANAGE_FCONTEXT_LINK;
file_types["symbolic link"] = SEMANAGE_FCONTEXT_LINK;
file_types["-p"] = SEMANAGE_FCONTEXT_PIPE;
file_types["named pipe"] = SEMANAGE_FCONTEXT_PIPE;

try:
	import audit
	class logger:
		def __init__(self):
			self.audit_fd = audit.audit_open()

		def log(self, success, msg, name = "", sename = "", serole = "", serange = "", old_sename = "", old_serole = "", old_serange = ""):
			audit.audit_log_semanage_message(self.audit_fd, audit.AUDIT_USER_ROLE_CHANGE, sys.argv[0],str(msg), name, 0, sename, serole, serange, old_sename, old_serole, old_serange, "", "", "", success);
except:
	class logger:
		def log(self, success, msg, name = "", sename = "", serole = "", serange = "", old_sename = "", old_serole = "", old_serange = ""):
			if success == 1:
				message = "Successful: "
			else:
				message = "Failed: "
			message += " %s name=%s" % (msg,name)
			if sename != "":
				message += " sename=" + sename
			if old_sename != "":
				message += " old_sename=" + old_sename
			if serole != "":
				message += " role=" + serole
			if old_serole != "":
				message += " old_role=" + old_serole
			if serange != "" and serange != None:
				message += " MLSRange=" + serange
			if old_serange != "" and old_serange != None:
				message += " old_MLSRange=" + old_serange
			syslog.syslog(message);
			
mylog = logger()		

import xml.etree.ElementTree

booleans_dict={}
try:
       tree=xml.etree.ElementTree.parse("/usr/share/selinux/devel/policy.xml")
       for l in  tree.findall("layer"):
              for m in  l.findall("module"):
                     for b in  m.findall("tunable"):
                            desc = b.find("desc").find("p").text.strip("\n")
                            desc = re.sub("\n", " ", desc)
                            booleans_dict[b.get('name')] = (m.get("name"), b.get('dftval'), desc)
                     for b in  m.findall("bool"):
                            desc = b.find("desc").find("p").text.strip("\n")
                            desc = re.sub("\n", " ", desc)
                            booleans_dict[b.get('name')] = (m.get("name"), b.get('dftval'), desc)
              for i in  tree.findall("bool"):
                     desc = i.find("desc").find("p").text.strip("\n")
                     desc = re.sub("\n", " ", desc)
                     booleans_dict[i.get('name')] = (_("global"), i.get('dftval'), desc)
       for i in  tree.findall("tunable"):
              desc = i.find("desc").find("p").text.strip("\n")
              desc = re.sub("\n", " ", desc)
              booleans_dict[i.get('name')] = (_("global"), i.get('dftval'), desc)
except IOError, e:
       #print _("Failed to translate booleans.\n%s") % e
       pass

def boolean_desc(boolean):
       if boolean in booleans_dict:
              return _(booleans_dict[boolean][2])
       else:
              return boolean

def validate_level(raw):
	sensitivity = "s[0-9]*"
	category = "c[0-9]*"
	cat_range = category + "(\." + category +")?"
	categories = cat_range + "(\," + cat_range + ")*"
	reg = sensitivity + "(-" + sensitivity + ")?" + "(:" + categories + ")?"
	return re.search("^" + reg +"$",raw)

def translate(raw, prepend = 1):
        filler="a:b:c:"
        if prepend == 1:
		context = "%s%s" % (filler,raw)
	else:
		context = raw
	(rc, trans) = selinux.selinux_raw_to_trans_context(context)
	if rc != 0:
		return raw
	if prepend:
		trans = trans[len(filler):]
	if trans == "":
		return raw
	else:
		return trans
	
def untranslate(trans, prepend = 1):
        filler="a:b:c:"
 	if prepend == 1:
		context = "%s%s" % (filler,trans)
	else:
		context = trans

	(rc, raw) = selinux.selinux_trans_to_raw_context(context)
	if rc != 0:
		return trans
	if prepend:
		raw = raw[len(filler):]
	if raw == "":
		return trans
	else:
		return raw
	
class setransRecords:
	def __init__(self):
		if not is_mls_enabled:
			raise ValueError(_("translations not supported on non-MLS machines"))			
		self.filename = selinux.selinux_translations_path()
		try:
			fd = open(self.filename, "r")
			translations = fd.readlines()
			fd.close()
		except IOError, e:
			raise ValueError(_("Unable to open %s: translations not supported on non-MLS machines: %s") % (self.filename, e) )
			
		self.ddict = {}
		self.comments = []
		for r in translations:
			if len(r) == 0:
				continue
			i = r.strip()
			if i == "" or i[0] == "#":
				self.comments.append(r)
				continue
			i = i.split("=")
			if len(i) != 2:
				self.comments.append(r)
				continue
                        if self.ddict.has_key(i[0]) == 0:
                               self.ddict[i[0]] = i[1]

	def get_all(self):
		return self.ddict

	def out(self):
		rec = ""
		for c in self.comments:
			rec += c
		keys = self.ddict.keys()
		keys.sort()
		for k in keys:
			rec += "%s=%s\n" %  (k, self.ddict[k])
		return rec
	
	def list(self,heading = 1, locallist = 0):
		if heading:
			print "\n%-25s %s\n" % (_("Level"), _("Translation"))
		keys = self.ddict.keys()
		keys.sort()
		for k in keys:
			print "%-25s %s" % (k, self.ddict[k])
		
	def add(self, raw, trans):
		if trans.find(" ") >= 0:
			raise ValueError(_("Translations can not contain spaces '%s' ") % trans)

		if validate_level(raw) == None:
			raise ValueError(_("Invalid Level '%s' ") % raw)
		
		if self.ddict.has_key(raw):
			raise ValueError(_("%s already defined in translations") % raw)
		else:
			self.ddict[raw] = trans
		self.save()
	
	def modify(self, raw, trans):
		if trans.find(" ") >= 0:

			raise ValueError(_("Translations can not contain spaces '%s' ") % trans)
		if self.ddict.has_key(raw):
			self.ddict[raw] = trans
		else:
			raise ValueError(_("%s not defined in translations") % raw)
		self.save()
		
	def delete(self, raw):
		self.ddict.pop(raw)
		self.save()

	def save(self):
		(fd, newfilename) = tempfile.mkstemp('', self.filename)
		os.write(fd, self.out())
		os.close(fd)
		os.rename(newfilename, self.filename)
                os.system("/sbin/service mcstrans reload > /dev/null")

class semanageRecords:
	def __init__(self, store):
               global handle
                      
               if handle != None:
                      self.transaction = True
                      self.sh = handle
               else:
                      self.sh=get_handle(store)
                      self.transaction = False

        def deleteall(self):
               raise ValueError(_("Not yet implemented"))

        def begin(self):
               if self.transaction:
                      return
               rc = semanage_begin_transaction(self.sh)
               if rc < 0:
                      raise ValueError(_("Could not start semanage transaction"))
        def commit(self):
               if self.transaction:
                      return
               rc = semanage_commit(self.sh) 
               if rc < 0:
                      raise ValueError(_("Could not commit semanage transaction"))

class permissiveRecords(semanageRecords):
	def __init__(self, store):
               semanageRecords.__init__(self, store)

	def get_all(self):
               l = []
               (rc, mlist, number) = semanage_module_list(self.sh)
               if rc < 0:
                      raise ValueError(_("Could not list SELinux modules"))

               for i in range(number):
                      mod = semanage_module_list_nth(mlist, i)
                      name = semanage_module_get_name(mod)
                      if name and name.startswith("permissive_"):
                             l.append(name.split("permissive_")[1])
               return l

	def list(self,heading = 1, locallist = 0):
		if heading:
			print "\n%-25s\n" % (_("Permissive Types"))
                for t in self.get_all():
                       print t


	def add(self, type):
               name = "permissive_%s" % type
               dirname = "/var/lib/selinux"
               os.chdir(dirname)
               filename = "%s.te" % name
               modtxt = """
module %s 1.0;

require {
          type %s;
}

permissive %s;
""" % (name, type, type)
               fd = open(filename,'w')
               fd.write(modtxt)
               fd.close()
               mc = module.ModuleCompiler()
               mc.create_module_package(filename, 1)
               fd = open("permissive_%s.pp" % type)
               data = fd.read()
               fd.close()

               rc = semanage_module_install(self.sh, data, len(data));
               if rc < 0:
			raise ValueError(_("Could not set permissive domain %s (module installation failed)") % name)

               self.commit()

               for root, dirs, files in os.walk("tmp", topdown=False):
                      for name in files:
                             os.remove(os.path.join(root, name))
                      for name in dirs:
                             os.rmdir(os.path.join(root, name))

	def delete(self, name):
               for n in name.split():
                      rc = semanage_module_remove(self.sh, "permissive_%s" % n)
                      if rc < 0:
                             raise ValueError(_("Could not remove permissive domain %s (remove failed)") % name)
                      
               self.commit()
			
	def deleteall(self):
               l = self.get_all()
               if len(l) > 0:
                      all = " ".join(l)
                      self.delete(all)

class loginRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)

	def __add(self, name, sename, serange):
		if is_mls_enabled == 1:
			if serange == "":
				serange = "s0"
			else:
				serange = untranslate(serange)
			
		if sename == "":
			sename = "user_u"
			
		(rc,k) = semanage_seuser_key_create(self.sh, name)
		if rc < 0:
			raise ValueError(_("Could not create a key for %s") % name)

		(rc,exists) = semanage_seuser_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if login mapping for %s is defined") % name)
		if exists:
			raise ValueError(_("Login mapping for %s is already defined") % name)
                if name[0] == '%':
                       try:
                              grp.getgrnam(name[1:])
                       except:
                              raise ValueError(_("Linux Group %s does not exist") % name[1:])
                else:
                       try:
                              pwd.getpwnam(name)
                       except:
                              raise ValueError(_("Linux User %s does not exist") % name)

                (rc,u) = semanage_seuser_create(self.sh)
                if rc < 0:
                       raise ValueError(_("Could not create login mapping for %s") % name)

                rc = semanage_seuser_set_name(self.sh, u, name)
                if rc < 0:
                       raise ValueError(_("Could not set name for %s") % name)

                if serange != "":
                       rc = semanage_seuser_set_mlsrange(self.sh, u, serange)
                       if rc < 0:
                              raise ValueError(_("Could not set MLS range for %s") % name)

                rc = semanage_seuser_set_sename(self.sh, u, sename)
                if rc < 0:
                       raise ValueError(_("Could not set SELinux user for %s") % name)

                rc = semanage_seuser_modify_local(self.sh, k, u)
                if rc < 0:
                       raise ValueError(_("Could not add login mapping for %s") % name)

		semanage_seuser_key_free(k)
		semanage_seuser_free(u)

	def add(self, name, sename, serange):
		try:
                        self.begin()
                        self.__add(name, sename, serange)
                        self.commit()

		except ValueError, error:
			mylog.log(0, _("add SELinux user mapping"), name, sename, "", serange);
			raise error
		
		mylog.log(1, _("add SELinux user mapping"), name, sename, "", serange);

	def __modify(self, name, sename = "", serange = ""):
               if sename == "" and serange == "":
                      raise ValueError(_("Requires seuser or serange"))

               (rc,k) = semanage_seuser_key_create(self.sh, name)
               if rc < 0:
                      raise ValueError(_("Could not create a key for %s") % name)

               (rc,exists) = semanage_seuser_exists(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not check if login mapping for %s is defined") % name)
               if not exists:
                      raise ValueError(_("Login mapping for %s is not defined") % name)

               (rc,u) = semanage_seuser_query(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not query seuser for %s") % name)

               oldserange = semanage_seuser_get_mlsrange(u)
               oldsename = semanage_seuser_get_sename(u)
               if serange != "":
                      semanage_seuser_set_mlsrange(self.sh, u, untranslate(serange))
               else:
                      serange = oldserange

               if sename != "":
                      semanage_seuser_set_sename(self.sh, u, sename)
               else:
                      sename = oldsename

               rc = semanage_seuser_modify_local(self.sh, k, u)
               if rc < 0:
                      raise ValueError(_("Could not modify login mapping for %s") % name)

               semanage_seuser_key_free(k)
               semanage_seuser_free(u)

               mylog.log(1,"modify selinux user mapping", name, sename, "", serange, oldsename, "", oldserange);

	def modify(self, name, sename = "", serange = ""):
		try:
                        self.begin()
                        self.__modify(name, sename, serange)
                        self.commit()

		except ValueError, error:
			mylog.log(0,"modify selinux user mapping", name, sename,"", serange, "", "", "");
			raise error
		
	def __delete(self, name):
               (rc,k) = semanage_seuser_key_create(self.sh, name)
               if rc < 0:
                      raise ValueError(_("Could not create a key for %s") % name)

               (rc,exists) = semanage_seuser_exists(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not check if login mapping for %s is defined") % name)
               if not exists:
                      raise ValueError(_("Login mapping for %s is not defined") % name)

               (rc,exists) = semanage_seuser_exists_local(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not check if login mapping for %s is defined") % name)
               if not exists:
                      raise ValueError(_("Login mapping for %s is defined in policy, cannot be deleted") % name)

               rc = semanage_seuser_del_local(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not delete login mapping for %s") % name)

               semanage_seuser_key_free(k)

	def delete(self, name):
		try:
                       self.begin()
                       self.__delete(name)
                       self.commit()

		except ValueError, error:
			mylog.log(0,"delete SELinux user mapping", name);
			raise error
		
		mylog.log(1,"delete SELinux user mapping", name);

	def get_all(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.ulist) = semanage_seuser_list_local(self.sh)
                else:
                       (rc, self.ulist) = semanage_seuser_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list login mappings"))

		for u in self.ulist:
			name = semanage_seuser_get_name(u)
			ddict[name] = (semanage_seuser_get_sename(u), semanage_seuser_get_mlsrange(u))
		return ddict

	def list(self,heading = 1, locallist = 0):
		ddict = self.get_all(locallist)
		keys = ddict.keys()
		keys.sort()
		if is_mls_enabled == 1:
			if heading:
				print "\n%-25s %-25s %-25s\n" % (_("Login Name"), _("SELinux User"), _("MLS/MCS Range"))
			for k in keys:
				print "%-25s %-25s %-25s" % (k, ddict[k][0], translate(ddict[k][1]))
		else:
			if heading:
				print "\n%-25s %-25s\n" % (_("Login Name"), _("SELinux User"))
			for k in keys:
				print "%-25s %-25s" % (k, ddict[k][0])

class seluserRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)

	def __add(self, name, roles, selevel, serange, prefix):
		if is_mls_enabled == 1:
			if serange == "":
				serange = "s0"
			else:
				serange = untranslate(serange)
			
			if selevel == "":
				selevel = "s0"
			else:
				selevel = untranslate(selevel)
			
                if len(roles) < 1:
                       raise ValueError(_("You must add at least one role for %s") % name)
                       
                (rc,k) = semanage_user_key_create(self.sh, name)
                if rc < 0:
                       raise ValueError(_("Could not create a key for %s") % name)

                (rc,exists) = semanage_user_exists(self.sh, k)
                if rc < 0:
                       raise ValueError(_("Could not check if SELinux user %s is defined") % name)
                if exists:
                       raise ValueError(_("SELinux user %s is already defined") % name)

                (rc,u) = semanage_user_create(self.sh)
                if rc < 0:
                       raise ValueError(_("Could not create SELinux user for %s") % name)

                rc = semanage_user_set_name(self.sh, u, name)
                if rc < 0:
                       raise ValueError(_("Could not set name for %s") % name)

                for r in roles:
                       rc = semanage_user_add_role(self.sh, u, r)
                       if rc < 0:
                              raise ValueError(_("Could not add role %s for %s") % (r, name))

                if is_mls_enabled == 1:
                       rc = semanage_user_set_mlsrange(self.sh, u, serange)
                       if rc < 0:
                              raise ValueError(_("Could not set MLS range for %s") % name)

                       rc = semanage_user_set_mlslevel(self.sh, u, selevel)
                       if rc < 0:
                              raise ValueError(_("Could not set MLS level for %s") % name)
                rc = semanage_user_set_prefix(self.sh, u, prefix)
                if rc < 0:
                       raise ValueError(_("Could not add prefix %s for %s") % (r, prefix))
                (rc,key) = semanage_user_key_extract(self.sh,u)
                if rc < 0:
                       raise ValueError(_("Could not extract key for %s") % name)

                rc = semanage_user_modify_local(self.sh, k, u)
                if rc < 0:
                       raise ValueError(_("Could not add SELinux user %s") % name)

                semanage_user_key_free(k)
                semanage_user_free(u)

	def add(self, name, roles, selevel, serange, prefix):
		seroles = " ".join(roles)
                try:
                       self.begin()
                       self.__add( name, roles, selevel, serange, prefix)
                       self.commit()
		except ValueError, error:
			mylog.log(0,"add SELinux user record", name, name, seroles, serange)
			raise error
		
		mylog.log(1,"add SELinux user record", name, name, seroles, serange)

        def __modify(self, name, roles = [], selevel = "", serange = "", prefix = ""):
		oldroles = ""
		oldserange = ""
		newroles = string.join(roles, ' ');
                if prefix == "" and len(roles) == 0  and serange == "" and selevel == "":
                       if is_mls_enabled == 1:
                              raise ValueError(_("Requires prefix, roles, level or range"))
                       else:
                              raise ValueError(_("Requires prefix or roles"))

                (rc,k) = semanage_user_key_create(self.sh, name)
                if rc < 0:
                       raise ValueError(_("Could not create a key for %s") % name)

                (rc,exists) = semanage_user_exists(self.sh, k)
                if rc < 0:
                       raise ValueError(_("Could not check if SELinux user %s is defined") % name)
                if not exists:
                       raise ValueError(_("SELinux user %s is not defined") % name)

                (rc,u) = semanage_user_query(self.sh, k)
                if rc < 0:
                       raise ValueError(_("Could not query user for %s") % name)

                oldserange = semanage_user_get_mlsrange(u)
                (rc, rlist) = semanage_user_get_roles(self.sh, u)
                if rc >= 0:
                       oldroles = string.join(rlist, ' ');
                       newroles = newroles + ' ' + oldroles;


                if serange != "":
                       semanage_user_set_mlsrange(self.sh, u, untranslate(serange))
                if selevel != "":
                       semanage_user_set_mlslevel(self.sh, u, untranslate(selevel))

                if prefix != "":
                       semanage_user_set_prefix(self.sh, u, prefix)

                if len(roles) != 0:
                       for r in rlist:
                              if r not in roles:
                                     semanage_user_del_role(u, r)
                       for r in roles:
                              if r not in rlist:
                                     semanage_user_add_role(self.sh, u, r)

                rc = semanage_user_modify_local(self.sh, k, u)
                if rc < 0:
                       raise ValueError(_("Could not modify SELinux user %s") % name)

		semanage_user_key_free(k)
		semanage_user_free(u)
		
		mylog.log(1,"modify SELinux user record", name, "", newroles, serange, "", oldroles, oldserange)


	def modify(self, name, roles = [], selevel = "", serange = "", prefix = ""):
		try:
                        self.begin()
                        self.__modify(name, roles, selevel, serange, prefix)
                        self.commit()

		except ValueError, error:
			mylog.log(0,"modify SELinux user record", name, "", " ".join(roles), serange, "", "", "")
			raise error

	def __delete(self, name):
               (rc,k) = semanage_user_key_create(self.sh, name)
               if rc < 0:
                      raise ValueError(_("Could not create a key for %s") % name)
			
               (rc,exists) = semanage_user_exists(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not check if SELinux user %s is defined") % name)		
               if not exists:
                      raise ValueError(_("SELinux user %s is not defined") % name)

               (rc,exists) = semanage_user_exists_local(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not check if SELinux user %s is defined") % name)
               if not exists:
                      raise ValueError(_("SELinux user %s is defined in policy, cannot be deleted") % name)
			
               rc = semanage_user_del_local(self.sh, k)
               if rc < 0:
                      raise ValueError(_("Could not delete SELinux user %s") % name)

               semanage_user_key_free(k)		

	def delete(self, name):
		try:
                        self.begin()
                        self.__delete(name)
                        self.commit()

		except ValueError, error:
			mylog.log(0,"delete SELinux user record", name)
			raise error
		
		mylog.log(1,"delete SELinux user record", name)

	def get_all(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.ulist) = semanage_user_list_local(self.sh)
                else:
                       (rc, self.ulist) = semanage_user_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list SELinux users"))

		for u in self.ulist:
			name = semanage_user_get_name(u)
			(rc, rlist) = semanage_user_get_roles(self.sh, u)
			if rc < 0:
				raise ValueError(_("Could not list roles for user %s") % name)

			roles = string.join(rlist, ' ');
			ddict[semanage_user_get_name(u)] = (semanage_user_get_prefix(u), semanage_user_get_mlslevel(u), semanage_user_get_mlsrange(u), roles)

		return ddict

	def list(self, heading = 1, locallist = 0):
		ddict = self.get_all(locallist)
		keys = ddict.keys()
		keys.sort()
		if is_mls_enabled == 1:
			if heading:
				print "\n%-15s %-10s %-10s %-30s" % ("", _("Labeling"), _("MLS/"), _("MLS/"))
				print "%-15s %-10s %-10s %-30s %s\n" % (_("SELinux User"), _("Prefix"), _("MCS Level"), _("MCS Range"), _("SELinux Roles"))
			for k in keys:
				print "%-15s %-10s %-10s %-30s %s" % (k, ddict[k][0], translate(ddict[k][1]), translate(ddict[k][2]), ddict[k][3])
		else:
			if heading:
				print "%-15s %s\n" % (_("SELinux User"), _("SELinux Roles"))
			for k in keys:
				print "%-15s %s" % (k, ddict[k][3])

class portRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)

	def __genkey(self, port, proto):
		if proto == "tcp":
			proto_d = SEMANAGE_PROTO_TCP
		else:
			if proto == "udp":
				proto_d = SEMANAGE_PROTO_UDP
			else:
				raise ValueError(_("Protocol udp or tcp is required"))
		if port == "":
			raise ValueError(_("Port is required"))
			
		ports = port.split("-")
		if len(ports) == 1:
			high = low = int(ports[0])
		else:
			low = int(ports[0])
			high = int(ports[1])

		(rc,k) = semanage_port_key_create(self.sh, low, high, proto_d)
		if rc < 0:
			raise ValueError(_("Could not create a key for %s/%s") % (proto, port))
		return ( k, proto_d, low, high )

	def __add(self, port, proto, serange, type):
		if is_mls_enabled == 1:
			if serange == "":
				serange = "s0"
			else:
				serange = untranslate(serange)
			
		if type == "":
			raise ValueError(_("Type is required"))

		( k, proto_d, low, high ) = self.__genkey(port, proto)			

		(rc,exists) = semanage_port_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if port %s/%s is defined") % (proto, port))
		if exists:
			raise ValueError(_("Port %s/%s already defined") % (proto, port))

		(rc,p) = semanage_port_create(self.sh)
		if rc < 0:
			raise ValueError(_("Could not create port for %s/%s") % (proto, port))
		
		semanage_port_set_proto(p, proto_d)
		semanage_port_set_range(p, low, high)
		(rc, con) = semanage_context_create(self.sh)
		if rc < 0:
			raise ValueError(_("Could not create context for %s/%s") % (proto, port))

		rc = semanage_context_set_user(self.sh, con, "system_u")
		if rc < 0:
			raise ValueError(_("Could not set user in port context for %s/%s") % (proto, port))

		rc = semanage_context_set_role(self.sh, con, "object_r")
		if rc < 0:
			raise ValueError(_("Could not set role in port context for %s/%s") % (proto, port))

		rc = semanage_context_set_type(self.sh, con, type)
		if rc < 0:
			raise ValueError(_("Could not set type in port context for %s/%s") % (proto, port))

		if serange != "":
			rc = semanage_context_set_mls(self.sh, con, serange)
			if rc < 0:
				raise ValueError(_("Could not set mls fields in port context for %s/%s") % (proto, port))

		rc = semanage_port_set_con(self.sh, p, con)
		if rc < 0:
			raise ValueError(_("Could not set port context for %s/%s") % (proto, port))

		rc = semanage_port_modify_local(self.sh, k, p)
		if rc < 0:
			raise ValueError(_("Could not add port %s/%s") % (proto, port))
	
		semanage_context_free(con)
		semanage_port_key_free(k)
		semanage_port_free(p)

	def add(self, port, proto, serange, type):
                self.begin()
                self.__add(port, proto, serange, type)
                self.commit()

	def __modify(self, port, proto, serange, setype):
		if serange == "" and setype == "":
			if is_mls_enabled == 1:
				raise ValueError(_("Requires setype or serange"))
			else:
				raise ValueError(_("Requires setype"))

		( k, proto_d, low, high ) = self.__genkey(port, proto)

		(rc,exists) = semanage_port_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if port %s/%s is defined") % (proto, port))
		if not exists:
			raise ValueError(_("Port %s/%s is not defined") % (proto,port))
	
		(rc,p) = semanage_port_query(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not query port %s/%s") % (proto, port))

		con = semanage_port_get_con(p)
			
		if serange != "":
			semanage_context_set_mls(self.sh, con, untranslate(serange))
		if setype != "":
			semanage_context_set_type(self.sh, con, setype)

		rc = semanage_port_modify_local(self.sh, k, p)
		if rc < 0:
			raise ValueError(_("Could not modify port %s/%s") % (proto, port))

		semanage_port_key_free(k)
		semanage_port_free(p)

	def modify(self, port, proto, serange, setype):
                self.begin()
                self.__modify(port, proto, serange, setype)
                self.commit()

	def deleteall(self):
		(rc, plist) = semanage_port_list_local(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list the ports"))

                self.begin()

		for port in plist:
                       proto = semanage_port_get_proto(port)
                       proto_str = semanage_port_get_proto_str(proto)
                       low = semanage_port_get_low(port)
                       high = semanage_port_get_high(port)
                       port_str = "%s-%s" % (low, high)
                       ( k, proto_d, low, high ) = self.__genkey(port_str , proto_str)
                       if rc < 0:
                              raise ValueError(_("Could not create a key for %s") % port_str)

                       rc = semanage_port_del_local(self.sh, k)
                       if rc < 0:
                              raise ValueError(_("Could not delete the port %s") % port_str)
                       semanage_port_key_free(k)
	
                self.commit()

	def __delete(self, port, proto):
		( k, proto_d, low, high ) = self.__genkey(port, proto)
		(rc,exists) = semanage_port_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if port %s/%s is defined") % (proto, port))
		if not exists:
			raise ValueError(_("Port %s/%s is not defined") % (proto, port))
		
		(rc,exists) = semanage_port_exists_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if port %s/%s is defined") % (proto, port))
		if not exists:
			raise ValueError(_("Port %s/%s is defined in policy, cannot be deleted") % (proto, port))

		rc = semanage_port_del_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not delete port %s/%s") % (proto, port))

		semanage_port_key_free(k)

	def delete(self, port, proto):
                self.begin()
                self.__delete(port, proto)
                self.commit()

	def get_all(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.plist) = semanage_port_list_local(self.sh)
                else:
                       (rc, self.plist) = semanage_port_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list ports"))

		for port in self.plist:
			con = semanage_port_get_con(port)
			ctype = semanage_context_get_type(con)
			if ctype == "reserved_port_t":
				continue
			level = semanage_context_get_mls(con)
			proto = semanage_port_get_proto(port)
			proto_str = semanage_port_get_proto_str(proto)
			low = semanage_port_get_low(port)
			high = semanage_port_get_high(port)
			ddict[(low, high)] = (ctype, proto_str, level)
		return ddict

	def get_all_by_type(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.plist) = semanage_port_list_local(self.sh)
                else:
                       (rc, self.plist) = semanage_port_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list ports"))

		for port in self.plist:
			con = semanage_port_get_con(port)
			ctype = semanage_context_get_type(con)
			if ctype == "reserved_port_t":
				continue
			proto = semanage_port_get_proto(port)
			proto_str = semanage_port_get_proto_str(proto)
			low = semanage_port_get_low(port)
			high = semanage_port_get_high(port)
			if (ctype, proto_str) not in ddict.keys():
				ddict[(ctype,proto_str)] = []
			if low == high:
				ddict[(ctype,proto_str)].append("%d" % low)
			else:
				ddict[(ctype,proto_str)].append("%d-%d" % (low, high))
		return ddict

	def list(self, heading = 1, locallist = 0):
		if heading:
			print "%-30s %-8s %s\n" % (_("SELinux Port Type"), _("Proto"), _("Port Number"))
		ddict = self.get_all_by_type(locallist)
		keys = ddict.keys()
		keys.sort()
		for i in keys:
			rec = "%-30s %-8s " % i
			rec += "%s" % ddict[i][0]
			for p in ddict[i][1:]:
				rec += ", %s" % p
			print rec

class nodeRecords(semanageRecords):
       def __init__(self, store = ""):
               semanageRecords.__init__(self,store)

       def __add(self, addr, mask, proto, serange, ctype):
               if addr == "":
                       raise ValueError(_("Node Address is required"))

               if mask == "":
                       raise ValueError(_("Node Netmask is required"))

	       if proto == "ipv4":
                       proto = 0
               elif proto == "ipv6":
                       proto = 1
               else:
                      raise ValueError(_("Unknown or missing protocol"))


               if is_mls_enabled == 1:
                       if serange == "":
                               serange = "s0"
                       else:
                               serange = untranslate(serange)

               if ctype == "":
                       raise ValueError(_("SELinux Type is required"))

               (rc,k) = semanage_node_key_create(self.sh, addr, mask, proto)
               if rc < 0:
                       raise ValueError(_("Could not create key for %s") % addr)
               if rc < 0:
                       raise ValueError(_("Could not check if addr %s is defined") % addr)

               (rc,exists) = semanage_node_exists(self.sh, k)
               if exists:
                       raise ValueError(_("Addr %s already defined") % addr)

               (rc,node) = semanage_node_create(self.sh)
               if rc < 0:
                       raise ValueError(_("Could not create addr for %s") % addr)

               rc = semanage_node_set_addr(self.sh, node, proto, addr)
               (rc, con) = semanage_context_create(self.sh)
               if rc < 0:
                       raise ValueError(_("Could not create context for %s") % addr)

               rc = semanage_node_set_mask(self.sh, node, proto, mask)
               if rc < 0:
                       raise ValueError(_("Could not set mask for %s") % addr)


               rc = semanage_context_set_user(self.sh, con, "system_u")
               if rc < 0:
                       raise ValueError(_("Could not set user in addr context for %s") % addr)

               rc = semanage_context_set_role(self.sh, con, "object_r")
               if rc < 0:
                       raise ValueError(_("Could not set role in addr context for %s") % addr)

               rc = semanage_context_set_type(self.sh, con, ctype)
               if rc < 0:
                       raise ValueError(_("Could not set type in addr context for %s") % addr)

               if serange != "":
                       rc = semanage_context_set_mls(self.sh, con, serange)
                       if rc < 0:
                               raise ValueError(_("Could not set mls fields in addr context for %s") % addr)

               rc = semanage_node_set_con(self.sh, node, con)
               if rc < 0:
                       raise ValueError(_("Could not set addr context for %s") % addr)

               rc = semanage_node_modify_local(self.sh, k, node)
               if rc < 0:
                       raise ValueError(_("Could not add addr %s") % addr)

               semanage_context_free(con)
               semanage_node_key_free(k)
               semanage_node_free(node)

       def add(self, addr, mask, proto, serange, ctype):
                self.begin()
                self.__add(self, addr, mask, proto, serange, ctype)
                self.commit()

       def __modify(self, addr, mask, proto, serange, setype):
               if addr == "":
                       raise ValueError(_("Node Address is required"))

               if mask == "":
                       raise ValueError(_("Node Netmask is required"))
               if proto == "ipv4":
                       proto = 0
               elif proto == "ipv6":
                       proto = 1
	       else:
		      raise ValueError(_("Unknown or missing protocol"))


               if serange == "" and setype == "":
                       raise ValueError(_("Requires setype or serange"))

               (rc,k) = semanage_node_key_create(self.sh, addr, mask, proto)
               if rc < 0:
                       raise ValueError(_("Could not create key for %s") % addr)

               (rc,exists) = semanage_node_exists(self.sh, k)
               if rc < 0:
                       raise ValueError(_("Could not check if addr %s is defined") % addr)
               if not exists:
                       raise ValueError(_("Addr %s is not defined") % addr)

               (rc,node) = semanage_node_query(self.sh, k)
               if rc < 0:
                       raise ValueError(_("Could not query addr %s") % addr)

               con = semanage_node_get_con(node)

               if serange != "":
                       semanage_context_set_mls(self.sh, con, untranslate(serange))
               if setype != "":
                       semanage_context_set_type(self.sh, con, setype)

               rc = semanage_node_modify_local(self.sh, k, node)
               if rc < 0:
                       raise ValueError(_("Could not modify addr %s") % addr)

               semanage_node_key_free(k)
               semanage_node_free(node)

       def modify(self, addr, mask, proto, serange, setype):
                self.begin()
                self.__modify(addr, mask, proto, serange, setype)
                self.commit()

       def __delete(self, addr, mask, proto):
               if addr == "":
                       raise ValueError(_("Node Address is required"))

               if mask == "":
                       raise ValueError(_("Node Netmask is required"))

	       if proto == "ipv4":
                       proto = 0
               elif proto == "ipv6":
                       proto = 1
               else:
                      raise ValueError(_("Unknown or missing protocol"))

               (rc,k) = semanage_node_key_create(self.sh, addr, mask, proto)
               if rc < 0:
                       raise ValueError(_("Could not create key for %s") % addr)

               (rc,exists) = semanage_node_exists(self.sh, k)
               if rc < 0:
                       raise ValueError(_("Could not check if addr %s is defined") % addr)
               if not exists:
                       raise ValueError(_("Addr %s is not defined") % addr)

               (rc,exists) = semanage_node_exists_local(self.sh, k)
               if rc < 0:
                       raise ValueError(_("Could not check if addr %s is defined") % addr)
               if not exists:
                       raise ValueError(_("Addr %s is defined in policy, cannot be deleted") % addr)

               rc = semanage_node_del_local(self.sh, k)
               if rc < 0:
                       raise ValueError(_("Could not delete addr %s") % addr)

               semanage_node_key_free(k)

       def delete(self, addr, mask, proto):
              self.begin()
              self.__delete(addr, mask, proto)
              self.commit()
		
       def get_all(self, locallist = 0):
               ddict = {}
	       if locallist :
			(rc, self.ilist) = semanage_node_list_local(self.sh)
	       else:
	                (rc, self.ilist) = semanage_node_list(self.sh)
               if rc < 0:
                       raise ValueError(_("Could not list addrs"))

               for node in self.ilist:
                       con = semanage_node_get_con(node)
                       addr = semanage_node_get_addr(self.sh, node)
                       mask = semanage_node_get_mask(self.sh, node)
                       proto = semanage_node_get_proto(node)
		       if proto == 0:
				proto = "ipv4"
		       elif proto == 1:
				proto = "ipv6"
                       ddict[(addr[1], mask[1], proto)] = (semanage_context_get_user(con), semanage_context_get_role(con), semanage_context_get_type(con), semanage_context_get_mls(con))

               return ddict

       def list(self, heading = 1, locallist = 0):
               if heading:
                       print "%-18s %-18s %-5s %-5s\n" % ("IP Address", "Netmask", "Protocol", "Context")
               ddict = self.get_all(locallist)
               keys = ddict.keys()
               keys.sort()
               if is_mls_enabled:
			for k in keys:
				val = ''
				for fields in k:
					val = val + '\t' + str(fields)
                                print "%-18s %-18s %-5s %s:%s:%s:%s " % (k[0],k[1],k[2],ddict[k][0], ddict[k][1],ddict[k][2], translate(ddict[k][3], False))
               else:
                       for k in keys:
                               print "%-18s %-18s %-5s %s:%s:%s " % (k[0],k[1],k[2],ddict[k][0], ddict[k][1],ddict[k][2])


class interfaceRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)

	def __add(self, interface, serange, ctype):
		if is_mls_enabled == 1:
			if serange == "":
				serange = "s0"
			else:
				serange = untranslate(serange)
			
		if ctype == "":
			raise ValueError(_("SELinux Type is required"))

		(rc,k) = semanage_iface_key_create(self.sh, interface)
		if rc < 0:
			raise ValueError(_("Could not create key for %s") % interface)

		(rc,exists) = semanage_iface_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if interface %s is defined") % interface)
		if exists:
			raise ValueError(_("Interface %s already defined") % interface)

		(rc,iface) = semanage_iface_create(self.sh)
		if rc < 0:
			raise ValueError(_("Could not create interface for %s") % interface)
		
		rc = semanage_iface_set_name(self.sh, iface, interface)
		(rc, con) = semanage_context_create(self.sh)
		if rc < 0:
			raise ValueError(_("Could not create context for %s") % interface)

		rc = semanage_context_set_user(self.sh, con, "system_u")
		if rc < 0:
			raise ValueError(_("Could not set user in interface context for %s") % interface)

		rc = semanage_context_set_role(self.sh, con, "object_r")
		if rc < 0:
			raise ValueError(_("Could not set role in interface context for %s") % interface)

		rc = semanage_context_set_type(self.sh, con, ctype)
		if rc < 0:
			raise ValueError(_("Could not set type in interface context for %s") % interface)

		if serange != "":
			rc = semanage_context_set_mls(self.sh, con, serange)
			if rc < 0:
				raise ValueError(_("Could not set mls fields in interface context for %s") % interface)

		rc = semanage_iface_set_ifcon(self.sh, iface, con)
		if rc < 0:
			raise ValueError(_("Could not set interface context for %s") % interface)

		rc = semanage_iface_set_msgcon(self.sh, iface, con)
		if rc < 0:
			raise ValueError(_("Could not set message context for %s") % interface)

		rc = semanage_iface_modify_local(self.sh, k, iface)
		if rc < 0:
			raise ValueError(_("Could not add interface %s") % interface)

		semanage_context_free(con)
		semanage_iface_key_free(k)
		semanage_iface_free(iface)

	def add(self, interface, serange, ctype):
                self.begin()
                self.__add(interface, serange, ctype)
                self.commit()

	def __modify(self, interface, serange, setype):
		if serange == "" and setype == "":
			raise ValueError(_("Requires setype or serange"))

		(rc,k) = semanage_iface_key_create(self.sh, interface)
		if rc < 0:
			raise ValueError(_("Could not create key for %s") % interface)

		(rc,exists) = semanage_iface_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if interface %s is defined") % interface)
		if not exists:
			raise ValueError(_("Interface %s is not defined") % interface)
	
		(rc,iface) = semanage_iface_query(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not query interface %s") % interface)

		con = semanage_iface_get_ifcon(iface)
			
		if serange != "":
			semanage_context_set_mls(self.sh, con, untranslate(serange))
		if setype != "":
			semanage_context_set_type(self.sh, con, setype)

		rc = semanage_iface_modify_local(self.sh, k, iface)
		if rc < 0:
			raise ValueError(_("Could not modify interface %s") % interface)
		
		semanage_iface_key_free(k)
		semanage_iface_free(iface)

	def modify(self, interface, serange, setype):
                self.begin()
                self.__modify(interface, serange, setype)
                self.commit()

	def __delete(self, interface):
		(rc,k) = semanage_iface_key_create(self.sh, interface)
		if rc < 0:
			raise ValueError(_("Could not create key for %s") % interface)

		(rc,exists) = semanage_iface_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if interface %s is defined") % interface)
		if not exists:
			raise ValueError(_("Interface %s is not defined") % interface)

		(rc,exists) = semanage_iface_exists_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if interface %s is defined") % interface)
		if not exists:
			raise ValueError(_("Interface %s is defined in policy, cannot be deleted") % interface)

		rc = semanage_iface_del_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not delete interface %s") % interface)

		semanage_iface_key_free(k)

	def delete(self, interface):
                self.begin()
                self.__delete(interface)
                self.commit()
		
	def get_all(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.ilist) = semanage_iface_list_local(self.sh)
                else:
                       (rc, self.ilist) = semanage_iface_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list interfaces"))

		for interface in self.ilist:
			con = semanage_iface_get_ifcon(interface)
			ddict[semanage_iface_get_name(interface)] = (semanage_context_get_user(con), semanage_context_get_role(con), semanage_context_get_type(con), semanage_context_get_mls(con))

		return ddict
			
	def list(self, heading = 1, locallist = 0):
		if heading:
			print "%-30s %s\n" % (_("SELinux Interface"), _("Context"))
		ddict = self.get_all(locallist)
		keys = ddict.keys()
		keys.sort()
		if is_mls_enabled:
			for k in keys:
				print "%-30s %s:%s:%s:%s " % (k,ddict[k][0], ddict[k][1],ddict[k][2], translate(ddict[k][3], False))
		else:
			for k in keys:
				print "%-30s %s:%s:%s " % (k,ddict[k][0], ddict[k][1],ddict[k][2])
			
class fcontextRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)

        def createcon(self, target, seuser = "system_u"):
                (rc, con) = semanage_context_create(self.sh)
                if rc < 0:
                       raise ValueError(_("Could not create context for %s") % target)
		if seuser == "":
			seuser = "system_u"

                rc = semanage_context_set_user(self.sh, con, seuser)
                if rc < 0:
                       raise ValueError(_("Could not set user in file context for %s") % target)
		
                rc = semanage_context_set_role(self.sh, con, "object_r")
                if rc < 0:
                       raise ValueError(_("Could not set role in file context for %s") % target)

		if is_mls_enabled == 1:
                       rc = semanage_context_set_mls(self.sh, con, "s0")
                       if rc < 0:
                              raise ValueError(_("Could not set mls fields in file context for %s") % target)

                return con
               
        def validate(self, target):
               if target == "" or target.find("\n") >= 0:
                      raise ValueError(_("Invalid file specification"))
                      
	def __add(self, target, type, ftype = "", serange = "", seuser = "system_u"):
                self.validate(target)

		if is_mls_enabled == 1:
                       serange = untranslate(serange)
			
		if type == "":
			raise ValueError(_("SELinux Type is required"))

		(rc,k) = semanage_fcontext_key_create(self.sh, target, file_types[ftype])
		if rc < 0:
			raise ValueError(_("Could not create key for %s") % target)

		(rc,exists) = semanage_fcontext_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if file context for %s is defined") % target)
		if exists:
			raise ValueError(_("File context for %s already defined") % target)

		(rc,fcontext) = semanage_fcontext_create(self.sh)
		if rc < 0:
			raise ValueError(_("Could not create file context for %s") % target)
		
		rc = semanage_fcontext_set_expr(self.sh, fcontext, target)
                if type != "<<none>>":
                       con = self.createcon(target, seuser)

                       rc = semanage_context_set_type(self.sh, con, type)
                       if rc < 0:
                              raise ValueError(_("Could not set type in file context for %s") % target)

                       if serange != "":
                              rc = semanage_context_set_mls(self.sh, con, serange)
                              if rc < 0:
                                     raise ValueError(_("Could not set mls fields in file context for %s") % target)
                       rc = semanage_fcontext_set_con(self.sh, fcontext, con)
                       if rc < 0:
                              raise ValueError(_("Could not set file context for %s") % target)

		semanage_fcontext_set_type(fcontext, file_types[ftype])

		rc = semanage_fcontext_modify_local(self.sh, k, fcontext)
		if rc < 0:
			raise ValueError(_("Could not add file context for %s") % target)

                if type != "<<none>>":
                       semanage_context_free(con)
		semanage_fcontext_key_free(k)
		semanage_fcontext_free(fcontext)

	def add(self, target, type, ftype = "", serange = "", seuser = "system_u"):
                self.begin()
                self.__add(target, type, ftype, serange, seuser)
                self.commit()

	def __modify(self, target, setype, ftype, serange, seuser):
		if serange == "" and setype == "" and seuser == "":
			raise ValueError(_("Requires setype, serange or seuser"))
                self.validate(target)

		(rc,k) = semanage_fcontext_key_create(self.sh, target, file_types[ftype])
		if rc < 0:
			raise ValueError(_("Could not create a key for %s") % target)

		(rc,exists) = semanage_fcontext_exists_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if file context for %s is defined") % target)
		if not exists:
			raise ValueError(_("File context for %s is not defined") % target)
		
		(rc,fcontext) = semanage_fcontext_query_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not query file context for %s") % target)

                if setype != "<<none>>":
                       con = semanage_fcontext_get_con(fcontext)
			
                       if con == None:
                              con = self.createcon(target)
                              
                       if serange != "":
                              semanage_context_set_mls(self.sh, con, untranslate(serange))
                       if seuser != "":
                              semanage_context_set_user(self.sh, con, seuser)
                              
                       if setype != "":
                              semanage_context_set_type(self.sh, con, setype)

                       rc = semanage_fcontext_set_con(self.sh, fcontext, con)
                       if rc < 0:
                              raise ValueError(_("Could not set file context for %s") % target)
                else:
                       rc = semanage_fcontext_set_con(self.sh, fcontext, None)
                       if rc < 0:
                              raise ValueError(_("Could not set file context for %s") % target)
                       
		rc = semanage_fcontext_modify_local(self.sh, k, fcontext)
		if rc < 0:
			raise ValueError(_("Could not modify file context for %s") % target)

		semanage_fcontext_key_free(k)
		semanage_fcontext_free(fcontext)

	def modify(self, target, setype, ftype, serange, seuser):
                self.begin()
                self.__modify(target, setype, ftype, serange, seuser)
                self.commit()
		

	def deleteall(self):
		(rc, flist) = semanage_fcontext_list_local(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list the file contexts"))

                self.begin()

		for fcontext in flist:
                       target = semanage_fcontext_get_expr(fcontext)
                       ftype = semanage_fcontext_get_type(fcontext)
                       ftype_str = semanage_fcontext_get_type_str(ftype)
                       (rc,k) = semanage_fcontext_key_create(self.sh, target, file_types[ftype_str])
                       if rc < 0:
                              raise ValueError(_("Could not create a key for %s") % target)

                       rc = semanage_fcontext_del_local(self.sh, k)
                       if rc < 0:
                              raise ValueError(_("Could not delete the file context %s") % target)
                       semanage_fcontext_key_free(k)
	
                self.commit()

	def __delete(self, target, ftype):
		(rc,k) = semanage_fcontext_key_create(self.sh, target, file_types[ftype])
		if rc < 0:
			raise ValueError(_("Could not create a key for %s") % target)

		(rc,exists) = semanage_fcontext_exists_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if file context for %s is defined") % target)
		if not exists:
			(rc,exists) = semanage_fcontext_exists(self.sh, k)
			if rc < 0:
				raise ValueError(_("Could not check if file context for %s is defined") % target)
			if exists:
				raise ValueError(_("File context for %s is defined in policy, cannot be deleted") % target)
			else:
				raise ValueError(_("File context for %s is not defined") % target)

		rc = semanage_fcontext_del_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not delete file context for %s") % target)

		semanage_fcontext_key_free(k)		

	def delete(self, target, ftype):
                self.begin()
                self.__delete( target, ftype)
                self.commit()

	def get_all(self, locallist = 0):
		l = []
                if locallist:
                       (rc, self.flist) = semanage_fcontext_list_local(self.sh)
                else:
                       (rc, self.flist) = semanage_fcontext_list(self.sh)
                       if rc < 0:
                              raise ValueError(_("Could not list file contexts"))

                       (rc, fclocal) = semanage_fcontext_list_local(self.sh)
                       if rc < 0:
                              raise ValueError(_("Could not list local file contexts"))

                       self.flist += fclocal

		for fcontext in self.flist:
			expr = semanage_fcontext_get_expr(fcontext)
			ftype = semanage_fcontext_get_type(fcontext)
			ftype_str = semanage_fcontext_get_type_str(ftype)
			con = semanage_fcontext_get_con(fcontext)
			if con:
				l.append((expr, ftype_str, semanage_context_get_user(con), semanage_context_get_role(con), semanage_context_get_type(con), semanage_context_get_mls(con)))
			else:
				l.append((expr, ftype_str, con))

		return l
			
	def list(self, heading = 1, locallist = 0 ):
		if heading:
			print "%-50s %-18s %s\n" % (_("SELinux fcontext"), _("type"), _("Context"))
		fcon_list = self.get_all(locallist)
		for fcon in fcon_list:
			if len(fcon) > 3:
				if is_mls_enabled:
					print "%-50s %-18s %s:%s:%s:%s " % (fcon[0], fcon[1], fcon[2], fcon[3], fcon[4], translate(fcon[5],False))
				else:
					print "%-50s %-18s %s:%s:%s " % (fcon[0], fcon[1], fcon[2], fcon[3],fcon[4])
			else:
				print "%-50s %-18s <<None>>" % (fcon[0], fcon[1])
				
class booleanRecords(semanageRecords):
	def __init__(self, store = ""):
		semanageRecords.__init__(self, store)
                self.dict={}
                self.dict["TRUE"] = 1
                self.dict["FALSE"] = 0
                self.dict["ON"] = 1
                self.dict["OFF"] = 0
                self.dict["1"] = 1
                self.dict["0"] = 0

	def __mod(self, name, value):
                (rc,k) = semanage_bool_key_create(self.sh, name)
                if rc < 0:
                       raise ValueError(_("Could not create a key for %s") % name)
                (rc,exists) = semanage_bool_exists(self.sh, k)
                if rc < 0:
                       raise ValueError(_("Could not check if boolean %s is defined") % name)
                if not exists:
                       raise ValueError(_("Boolean %s is not defined") % name)	
                
                (rc,b) = semanage_bool_query(self.sh, k)
                if rc < 0:
                       raise ValueError(_("Could not query file context %s") % name)

                if value.upper() in self.dict:
                       semanage_bool_set_value(b, self.dict[value.upper()])
                else:
                       raise ValueError(_("You must specify one of the following values: %s") % ", ".join(self.dict.keys()) )
                
                rc = semanage_bool_set_active(self.sh, k, b)
                if rc < 0:
                       raise ValueError(_("Could not set active value of boolean %s") % name)
                rc = semanage_bool_modify_local(self.sh, k, b)
                if rc < 0:
                       raise ValueError(_("Could not modify boolean %s") % name)
		semanage_bool_key_free(k)
		semanage_bool_free(b)

	def modify(self, name, value=None, use_file=False):
                
                self.begin()

                if use_file:
                       fd = open(name)
                       for b in fd.read().split("\n"):
                              b = b.strip()
                              if len(b) == 0:
                                     continue

                              try:
                                     boolname, val = b.split("=")
                              except ValueError:
                                     raise ValueError(_("Bad format %s: Record %s" % ( name, b) ))
                              self.__mod(boolname.strip(), val.strip())
                       fd.close()
                else:
                       self.__mod(name, value)

                self.commit()
		
	def __delete(self, name):

                (rc,k) = semanage_bool_key_create(self.sh, name)
                if rc < 0:
                      raise ValueError(_("Could not create a key for %s") % name)
		(rc,exists) = semanage_bool_exists(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if boolean %s is defined") % name)
		if not exists:
			raise ValueError(_("Boolean %s is not defined") % name)
	
		(rc,exists) = semanage_bool_exists_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not check if boolean %s is defined") % name)
		if not exists:
			raise ValueError(_("Boolean %s is defined in policy, cannot be deleted") % name)

		rc = semanage_bool_del_local(self.sh, k)
		if rc < 0:
			raise ValueError(_("Could not delete boolean %s") % name)
	
		semanage_bool_key_free(k)

	def delete(self, name):
                self.begin()
                self.__delete(name)
                self.commit()

	def deleteall(self):
		(rc, self.blist) = semanage_bool_list_local(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list booleans"))

                self.begin()

		for boolean in self.blist:
                       name = semanage_bool_get_name(boolean)
                       self.__delete(name)

                self.commit()
	
	def get_all(self, locallist = 0):
		ddict = {}
                if locallist:
                       (rc, self.blist) = semanage_bool_list_local(self.sh)
                else:
                       (rc, self.blist) = semanage_bool_list(self.sh)
		if rc < 0:
			raise ValueError(_("Could not list booleans"))

		for boolean in self.blist:
                       value = []
                       name = semanage_bool_get_name(boolean)
                       value.append(semanage_bool_get_value(boolean))
                       value.append(selinux.security_get_boolean_pending(name))
                       value.append(selinux.security_get_boolean_active(name))
                       ddict[name] = value

		return ddict
			
        def get_desc(self, boolean):
               return boolean_desc(boolean)

        def get_category(self, boolean):
               if boolean in booleans_dict:
                      return _(booleans_dict[boolean][0])
               else:
                      return _("unknown")

	def list(self, heading = True, locallist = False, use_file = False):
                on_off = (_("off"),_("on")) 
		if use_file:
                       ddict = self.get_all(locallist)
                       keys = ddict.keys()
                       for k in keys:
                              if ddict[k]:
                                     print "%s=%s" %  (k, ddict[k][2])
                       return
		if heading:
			print "%-40s %s\n" % (_("SELinux boolean"), _("Description"))
		ddict = self.get_all(locallist)
		keys = ddict.keys()
		for k in keys:
			if ddict[k]:
				print "%-30s -> %-5s %s" %  (k, on_off[ddict[k][2]], self.get_desc(k))

