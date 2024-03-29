#! /usr/bin/python

# Code generator for python ctypes bindings for VLC
#
# Copyright (C) 2009-2012 the VideoLAN team
# $Id: $
#
# Authors: Olivier Aubert <olivier.aubert at liris.cnrs.fr>
#          Jean Brouwers <MrJean1 at gmail.com>
#          Geoff Salmon <geoff.salmon at gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston MA 02110-1301, USA.

"""This module parses VLC public API include files and generates
corresponding Python/ctypes bindingsB{**} code.  Moreover, it
generates Python class and method wrappers from the VLC functions
and enum types.

There are 3 dependencies.  Files C{header.py} and C{footer.py}
contain the pre- and postamble of the generated code.  Module
C{override.py} defines a number of classes and methods overriding
the ones to be generated.

This module and the generated Python bindings have been verified
with PyChecker 0.8.18, see U{http://pychecker.sourceforge.net}
and PyFlakes 0.4.0, see U{http://pypi.python.org/pypi/pyflakes}.
The C{#PYCHOK ...} comments direct the PyChecker/-Flakes post-
processor, see U{http://code.activestate.com/recipes/546532}.

This module and the generated Python bindings have been tested with
32-bit Python 2.6, 2.7 and 3.1 on Linux, Windows XP SP3 and MacOS X 10.4.11
(Intel) using the VLC 1.1.4.1 and 1.1.5 public API include files.

B{**)} The Java/JNA bindings for the VLC public API can be created
in a similar manner and depend on 3 Java files: C{boilerplate.java},
C{LibVlc-footer.java} and C{LibVlc-header.java}.

"""
__all__     = ('Parser',
               'PythonGenerator', 'JavaGenerator',
               'process')
__version__ =  '20.12.04.27'

_debug = False

import sys
import os
import re
import time
import operator

# Opener for text files
if sys.hexversion < 0x3000000:
    def opener(name, mode='r'):
        return open(name, mode)
else:  # Python 3+
    def opener(name, mode='r'):  #PYCHOK expected
        return open(name, mode, encoding='utf8')

# Functions not wrapped/not referenced
_blacklist = {
    # Deprecated functions
    'libvlc_audio_output_set_device_type': '',
    'libvlc_audio_output_get_device_type': '',
    'libvlc_set_exit_handler':    '',
    'libvlc_printerr': '',
}

# Set of functions that return a string that the caller is
# expected to free.
free_string_funcs = set((
        'libvlc_media_discoverer_localized_name',
        'libvlc_media_get_mrl',
        'libvlc_media_get_meta',
        'libvlc_video_get_aspect_ratio',
        'libvlc_video_get_crop_geometry',
        'libvlc_video_get_marquee_string',
        'libvlc_audio_output_device_longname',
        'libvlc_audio_output_device_id',
        'libvlc_vlm_show_media',
    ))

# some constants
_NA_     = 'N/A'
_NL_     = '\n'  # os.linesep
_OUT_    = '[OUT]'
_PNTR_   = 'pointer to get the '  # KLUDGE: see @param ... [OUT]
_INDENT_ = '    '

# special keywords in header.py
_BUILD_DATE_      = 'build_date  = '
_GENERATED_ENUMS_ = '# GENERATED_ENUMS'

# keywords in header files
_VLC_FORWARD_     = 'VLC_FORWARD'
_VLC_PUBLIC_API_  = 'LIBVLC_API'

# Precompiled regexps
api_re       = re.compile(_VLC_PUBLIC_API_ + '\s+(\S+\s+.+?)\s*\(\s*(.+?)\s*\)')
at_param_re  = re.compile('(@param\s+\S+)(.+)')
bs_param_re  = re.compile('\\param\s+(\S+)')
class_re     = re.compile('class\s+(\S+):')
def_re       = re.compile('^\s+def\s+(\w+)', re.MULTILINE)
enum_type_re = re.compile('^(?:typedef\s+)?enum')
enum_re      = re.compile('(?:typedef\s+)?(enum)\s*(\S+)\s*\{\s*(.+)\s*\}\s*(?:\S+)?;')
enum_pair_re = re.compile('\s*=\s*')
callback_type_re = re.compile('^typedef\s+\w+(\s+\*)?\s*\(\s*\*')
callback_re  = re.compile('typedef\s+\*?(\w+\s*\*?)\s*\(\s*\*\s*(\w+)\s*\)\s*\((.+)\);')
forward_re   = re.compile('.+\(\s*(.+?)\s*\)(\s*\S+)')
libvlc_re    = re.compile('\slibvlc_[a-z_]+')
param_re     = re.compile('\s*(const\s*|unsigned\s*|struct\s*)?(\S+\s*\**)\s+(.+)')
paramlist_re = re.compile('\s*,\s*')
version_re   = re.compile('vlc[\-]\d+[.]\d+[.]\d+.*')

def endot(text):
    """Terminate string with a period.
    """
    if text and text[-1] not in '.,:;?!':
        text += '.'
    return text

def errorf(fmt, *args):
    """Print error.
    """
    global _nerrors
    _nerrors += 1
    sys.stderr.write('Error: ' + (fmt % args) + "\n")

_nerrors = 0

def errors(fmt, e=0):
    """Report total number of errors.
    """
    if _nerrors > e:
        n = _nerrors - e
        x =  min(n, 9)
        errorf(fmt + '... exit(%s)', n, x)
        sys.exit(x)
    elif _debug:
        sys.stderr.write(fmt % (_NL_ + 'No') + "\n")

class _Source(object):
    """Base class for elements parsed from source.
    """
    source = ''

    def __init__(self, file_='', line=0):
        self.source = '%s:%s' % (file_, line)
        self.dump()  #PYCHOK expected

class Enum(_Source):
    """Enum type.
    """
    type = 'enum'

    def __init__(self, name, type='enum', vals=(), docs='', **kwds):
        if type != self.type:
            raise TypeError('expected enum type: %s %s' % (type, name))
        self.docs = docs
        self.name = name
        self.vals = vals  # list/tuple of Val instances
        if _debug:
           _Source.__init__(self, **kwds)

    def check(self):
        """Perform some consistency checks.
        """
        if not self.docs:
            errorf('no comment for typedef %s %s', self.type, self.name)
        if self.type != 'enum':
            errorf('expected enum type: %s %s', self.type, self.name)

    def dump(self):  # for debug
        sys.stderr.write('%s (%s): %s\n' % (self.name, self.type, self.source))
        for v in self.vals:
            v.dump()

    def epydocs(self):
        """Return epydoc string.
        """
        return self.docs.replace('@see', 'See').replace('\\see', 'See')

class Flag(object):
    """Enum-like, ctypes parameter direction flag constants.
    """
    In     = 1  # input only
    Out    = 2  # output only
    InOut  = 3  # in- and output
    InZero = 4  # input, default int 0
    def __init__(self):
        raise TypeError('constants only')

class Func(_Source):
    """C function.
    """
    heads   = ()  # docs lines without most @tags
    out     = ()  # [OUT] parameter names
    params  = ()  # @param lines, except [OUT]
    tails   = ()  # @return, @version, @bug lines
    wrapped =  0  # number of times wrapped

    def __init__(self, name, type, pars=(), docs='', **kwds):
        self.docs = docs
        self.name = name
        self.pars = pars  # list/tuple of Par instances
        self.type = type
        if _debug:
           _Source.__init__(self, **kwds)

    def args(self, first=0):
        """Return the parameter names, excluding output parameters.
           Ctypes returns all output parameter values as part of
           the returned tuple.
        """
        return [p.name for p in self.in_params(first)]

    def in_params(self, first=0):
        """Return the parameters, excluding output parameters.
           Ctypes returns all output parameter values as part of
           the returned tuple.
        """
        return [p for p in self.pars[first:] if
                p.flags(self.out)[0] != Flag.Out]

    def check(self):
        """Perform some consistency checks.
        """
        if not self.docs:
            errorf('no comment for function %s', self.name)
        elif len(self.pars) != self.nparams:
            errorf('doc parameters (%d) mismatch for function %s (%d)',
                    self.nparams, self.name, len(self.pars))
            if _debug:
                self.dump()
                sys.stderr.write(self.docs + "\n")

    def dump(self):  # for debug
        sys.stderr.write('%s (%s): %s\n' %  (self.name, self.type, self.source))
        for p in self.pars:
            p.dump(self.out)

    def epydocs(self, first=0, indent=0):
        """Return epydoc doc string with/out first parameter.
        """
        # "out-of-bounds" slices are OK, e.g. ()[1:] == ()
        t = _NL_ + (' ' * indent)
        return t.join(self.heads + self.params[first:] + self.tails)

    def __nparams_(self):
        return (len(self.params) + len(self.out)) or len(bs_param_re.findall(self.docs))
    nparams = property(__nparams_, doc='number of \\param lines in doc string')

    def xform(self):
        """Transform Doxygen to epydoc syntax.
        """
        b, c, h, o, p, r, v = [], None, [], [], [], [], []
        # see <http://epydoc.sourceforge.net/manual-fields.html>
        # (or ...replace('{', 'E{lb}').replace('}', 'E{rb}') ?)
        for t in self.docs.replace('@{', '').replace('@}', '').replace('\\ingroup', '') \
                          .replace('{', '').replace('}', '') \
                          .replace('<b>', 'B{').replace('</b>', '}') \
                          .replace('@see', 'See').replace('\\see', 'See') \
                          .replace('\\bug', '@bug').replace('\\version', '@version') \
                          .replace('\\note', '@note').replace('\\warning', '@warning') \
                          .replace('\\param', '@param').replace('\\return', '@return') \
                          .splitlines():
            if '@param' in t:
                if _OUT_ in t:
                    # KLUDGE: remove @param, some comment and [OUT]
                    t = t.replace('@param', '').replace(_PNTR_, '').replace(_OUT_, '')
                    # keep parameter name and doc string
                    o.append(' '.join(t.split()))
                    c = ['']  # drop continuation line(s)
                else:
                    p.append(at_param_re.sub('\\1:\\2', t))
                    c = p
            elif '@return' in t:
                r.append(t.replace('@return ', '@return: '))
                c = r
            elif '@bug' in t:
                b.append(t.replace('@bug ', '@bug: '))
                c = b
            elif '@version' in t:
                v.append(t.replace('@version ', '@version: '))
                c = v
            elif c is None:
                h.append(t.replace('@note ', '@note: ').replace('@warning ', '@warning: '))
            else:  # continuation, concatenate to previous @tag line
                c[-1] = '%s %s' % (c[-1], t.strip())
        if h:
            h[-1] = endot(h[-1])
            self.heads = tuple(h)
        if o:  # just the [OUT] parameter names
            self.out = tuple(t.split()[0] for t in o)
            # ctypes returns [OUT] parameters as tuple
            r = ['@return: %s' % ', '.join(o)]
        if p:
            self.params = tuple(map(endot, p))
        t = r + v + b
        if t:
            self.tails = tuple(map(endot, t))

class Par(object):
    """C function parameter.
    """
    def __init__(self, name, type):
        self.name = name
        self.type = type  # C type

    def dump(self, out=()):  # for debug
        if self.name in out:
            t = _OUT_  # @param [OUT]
        else:
            t = {Flag.In:     '',  # default
                 Flag.Out:    'Out',
                 Flag.InOut:  'InOut',
                 Flag.InZero: 'InZero',
                }.get(self.flags()[0], 'FIXME_Flag')
        sys.stderr.write('%s%s (%s) %s\n' % (_INDENT_, self.name, self.type, t))

    # Parameter passing flags for types.  This shouldn't
    # be hardcoded this way, but works all right for now.
    def flags(self, out=(), default=None):
        """Return parameter flags tuple.

        Return the parameter flags tuple for the given parameter
        type and name and a list of parameter names documented as
        [OUT].
        """
        if self.name in out:
            f = Flag.Out  # @param [OUT]
        else:
            f = {'int*':      Flag.Out,
                 'unsigned*': Flag.Out,
                 'libvlc_media_track_info_t**': Flag.Out,
                }.get(self.type, Flag.In)  # default
        if default is None:
            return f,  # 1-tuple
        else:  # see ctypes 15.16.2.4 Function prototypes
            return f, self.name, default  #PYCHOK expected

class Val(object):
    """Enum name and value.
    """
    def __init__(self, enum, value):
        self.enum = enum  # C name
        # convert name
        t = enum.split('_')
        n = t[-1]
        if len(n) <= 1:  # single char name
            n = '_'.join( t[-2:] )  # some use 1_1, 5_1, etc.
        if n[0].isdigit():  # can't start with a number
            n = '_' + n
        self.name = n
        self.value = value

    def dump(self):  # for debug
        sys.stderr.write('%s%s = %s\n' % (_INDENT_, self.name, self.value))

class Parser(object):
    """Parser of C header files.
    """
    h_file = ''

    def __init__(self, h_files, version=''):
        self.enums = []
        self.callbacks = []
        self.funcs = []
        self.version = version

        for h in h_files:
            if not self.version:  # find vlc-... version
                for v in h.replace('\\', '/').split('/'):
                    if version_re.match(v):
                        self.version = v
                        break
            self.h_file = h
            self.enums.extend(self.parse_enums())
            self.callbacks.extend(self.parse_callbacks())
            self.funcs.extend(self.parse_funcs())

    def check(self):
        """Perform some consistency checks.
        """
        for e in self.enums:
            e.check()
        for f in self.funcs:
            f.check()
        for f in self.callbacks:
            f.check()

    def dump(self, attr):
        sys.stderr.write('%s==== %s ==== %s\n' % (_NL_, attr, self.version))
        for a in getattr(self, attr, ()):
            a.dump()

    def parse_callbacks(self):
        """Parse header file for callback signature definitions.

        @return: yield a Func instance for each callback signature, unless blacklisted.
        """
        for type_, name, pars, docs, line in self.parse_groups(callback_type_re.match, callback_re.match, ');'):

            pars = [self.parse_param(p) for p in paramlist_re.split(pars)]

            yield Func(name, type_.replace(' ', '') + '*', pars, docs,
                       file_=self.h_file, line=line)

    def parse_enums(self):
        """Parse header file for enum type definitions.

        @return: yield an Enum instance for each enum.
        """
        for typ, name, enum, docs, line in self.parse_groups(enum_type_re.match, enum_re.match):
            vals, v = [], -1  # enum value(s)
            for t in paramlist_re.split(enum):
                t = t.strip()
                if not t.startswith('/*'):
                    if '=' in t:  # has value
                        n, v = enum_pair_re.split(t)
                        vals.append(Val(n, v))
                        if v.startswith('0x'):  # '0X'?
                            v = int(v, 16)
                        else:
                            v = int(v)
                    elif t:  # only name
                        v += 1
                        vals.append(Val(t, str(v)))

            name = name.strip()
            if not name:  # anonymous
                name = 'libvlc_enum_t'

            # more doc string cleanup
            docs = endot(docs).capitalize()

            yield Enum(name, typ, vals, docs,
                       file_=self.h_file, line=line)

    def parse_funcs(self):
        """Parse header file for public function definitions.

        @return: yield a Func instance for each function, unless blacklisted.
        """
        def match_t(t):
            return t.startswith(_VLC_PUBLIC_API_)

        for name, pars, docs, line in self.parse_groups(match_t, api_re.match, ');'):

            f = self.parse_param(name)
            if f.name in _blacklist:
                _blacklist[f.name] = f.type
                continue

            pars = [self.parse_param(p) for p in paramlist_re.split(pars)]

            if len(pars) == 1 and pars[0].type == 'void':
                pars = []  # no parameters

            elif any(p for p in pars if not p.name):  # list(...)
                # no or missing parameter names, peek in doc string
                n = bs_param_re.findall(docs)
                if len(n) < len(pars):
                    errorf('%d parameter(s) missing in function %s comment: %s',
                            (len(pars) - len(n)), f.name, docs.replace(_NL_, ' ') or _NA_)
                    n.extend('param%d' % i for i in range(len(n), len(pars)))  #PYCHOK false?
                # FIXME: this assumes that the order of the parameters is
                # the same in the parameter list and in the doc string
                for i, p in enumerate(pars):
                    p.name = n[i]

            yield Func(f.name, f.type, pars, docs,
                       file_=self.h_file, line=line)

    def parse_groups(self, match_t, match_re, ends=';'):
        """Parse header file for matching lines, re and ends.

        @return: yield a tuple of re groups extended with the
        doc string and the line number in the header file.
        """
        a = []  # multi-lines
        d = []  # doc lines
        n = 0   # line number
        s = False  # skip comments except doc
        f = opener(self.h_file)
        for t in f:
            n += 1
            # collect doc lines
            if t.startswith('/**'):
                d =     [t[3:].rstrip()]
            elif t.startswith(' * '):  # FIXME: keep empty lines
                d.append(t[3:].rstrip())

            else:  # parse line
                t, m = t.strip(), None
                if s or t.startswith('/*'):  # in comment
                    s = not t.endswith('*/')

                elif a:  # accumulate multi-line
                    t = t.split('/*', 1)[0].rstrip()  # //?
                    a.append(t)
                    if t.endswith(ends):  # end
                        t = ' '.join(a)
                        m = match_re(t)
                        a = []
                elif match_t(t):
                    if t.endswith(ends):
                        m = match_re(t)  # single line
                    else:  # new multi-line
                        a = [t]

                if m:
                    # clean up doc string
                    d = _NL_.join(d).strip()
                    if d.endswith('*/'):
                        d = d[:-2].rstrip()

                    if _debug:
                        sys.stderr.write('%s==== source ==== %s:%d\n' % (_NL_, self.h_file, n))
                        sys.stderr.write(t + "\n")
                        sys.stderr.write('"""%s%s"""\n' % (d, _NL_))

                    yield m.groups() + (d, n)
                    d = []
        f.close()

    def parse_param(self, param):
        """Parse a C parameter expression.

        It is used to parse the type/name of functions
        and type/name of the function parameters.

        @return: a Par instance.
        """
        t = param.replace('const', '').strip()
        if _VLC_FORWARD_ in t:
            m = forward_re.match(t)
            t = m.group(1) + m.group(2)

        m = param_re.search(t)
        if m:
            _, t, n = m.groups()
            while n.startswith('*'):
                n  = n[1:].lstrip()
                t += '*'
##          if n == 'const*':
##              # K&R: [const] char* const*
##              n = ''
        else:  # K&R: only [const] type
            n = ''
        return Par(n, t.replace(' ', ''))


class _Generator(object):
    """Base class.
    """
    comment_line = '#'   # Python
    file         = None
    links        = {}    # must be overloaded
    outdir       = ''
    outpath      = ''
    type_re      = None  # must be overloaded
    type2class   = {}    # must be overloaded

    def __init__(self, parser=None):
      ##self.type2class = self.type2class.copy()
        self.parser = parser
        self.convert_enums()
        self.convert_callbacks()

    def check_types(self):
        """Make sure that all types are properly translated.

        @note: This method must be called B{after} C{convert_enums},
        since the latter populates C{type2class} with enum class names.
        """
        e = _nerrors
        for f in self.parser.funcs:
            if f.type not in self.type2class:
                errorf('no type conversion for %s %s', f.type, f.name)
            for p in f.pars:
                if p.type not in self.type2class:
                    errorf('no type conversion for %s %s in %s', p.type, p.name, f.name)
        errors('%s type conversion(s) missing', e)

    def class4(self, type):
        """Return the class name for a type or enum.
        """
        return self.type2class.get(type, '') or ('FIXME_%s' % (type,))

    def convert_enums(self):
        """Convert enum names to class names.
        """
        for e in self.parser.enums:
            if e.type != 'enum':
                raise TypeError('expected enum: %s %s' % (e.type, e.name))

            c = self.type_re.findall(e.name)[0][0]
            if '_' in c:
                c = c.title().replace('_', '')
            elif c[0].islower():
                c = c.capitalize()
            self.type2class[e.name] = c

    def convert_callbacks(self):
        """Convert callback names to class names.
        """
        for f in self.parser.callbacks:
            c = self.type_re.findall(f.name)[0][0]
            if '_' in c:
                c = c.title().replace('_', '')
            elif c[0].islower():
                c = c.capitalize()
            self.type2class[f.name] = c

    def dump_dicts(self):  # for debug
        s = _NL_ + _INDENT_
        for n in ('type2class', 'prefixes', 'links'):
            d = getattr(self, n, None)
            if d:
                n = ['%s==== %s ==== %s' % (_NL_, n, self.parser.version)]
                sys.stderr.write(s.join(n + sorted('%s: %s\n' % t for t in d.items())))

    def epylink(self, docs, striprefix=None):
        """Link function, method and type names in doc string.
        """
        def _L(m):  # re.sub callback
            t = m.group(0)
            n = t.strip()
            k = self.links.get(n, '')
            if k:
                if striprefix:
                    k = striprefix(k)
                t = t.replace(n, 'L{%s}' % (k,))
            return t

        if self.links:
            return libvlc_re.sub(_L, docs)
        else:
            return docs

    def generate_enums(self):
        raise TypeError('must be overloaded')

    def insert_code(self, source, genums=False):
        """Include code from source file.
        """
        f = opener(source)
        for t in f:
            if genums and t.startswith(_GENERATED_ENUMS_):
                self.generate_enums()
                self.generate_callbacks()
            elif t.startswith(_BUILD_DATE_):
                v, t = _NA_, self.parser.version
                if t:
                    v, t = t, ' ' + t
                self.output('__version__ = "%s"' % (v,))
                self.output('%s"%s%s"' % (_BUILD_DATE_, time.ctime(), t))
            else:
                self.output(t, nt=0)
        f.close()

    def outclose(self):
        """Close the output file.
        """
        if self.file not in (None, sys.stdout):
           self.file.close()
        self.file = None

    def outopen(self, name):
        """Open an output file.
        """
        if self.file:
            self.outclose()
            raise IOError('file left open: %s' % (self.outpath,))

        if name in ('-', 'stdout'):
            self.outpath = 'stdout'
            self.file = sys.stdout
        else:
            self.outpath = os.path.join(self.outdir, name)
            self.file = opener(self.outpath, 'w')

    def output(self, text, nl=0, nt=1):
        """Write to current output file.
        """
        if nl:  # leading newlines
            self.file.write(_NL_ * nl)
        self.file.write(text)
        if nt:  # trailing newlines
            self.file.write(_NL_ * nt)

    def unwrapped(self):
        """Report the unwrapped and blacklisted functions.
        """
        b = [f for f, t in _blacklist.items() if t]
        u = [f.name for f in self.parser.funcs if not f.wrapped]
        c = self.comment_line
        for f, t in ((b, 'blacklisted'),
                     (u, 'not wrapped as methods')):
            if f:
                self.output('%s %d function(s) %s:' % (c, len(f), t), nl=1)
                self.output(_NL_.join('%s  %s' % (c, f) for f in sorted(f)))  #PYCHOK false?


class PythonGenerator(_Generator):
    """Generate Python bindings.
    """
    type_re = re.compile('libvlc_(.+?)(_t)?$')  # Python

    # C-type to Python/ctypes type conversion.  Note, enum
    # type conversions are generated (cf convert_enums).
    type2class = {
        'libvlc_audio_output_t*':      'ctypes.POINTER(AudioOutput)',
        'libvlc_event_t*':              'ctypes.c_void_p',
        #'libvlc_callback_t':           'ctypes.c_void_p',
        'libvlc_drawable_t':           'ctypes.c_uint',  # FIXME?
        'libvlc_event_type_t':         'ctypes.c_uint',
        'libvlc_event_manager_t*':     'EventManager',
        'libvlc_instance_t*':          'Instance',
        'libvlc_log_t*':               'Log_ptr',
        'libvlc_log_iterator_t*':      'LogIterator',
        'libvlc_log_subscriber_t*':    'ctypes.c_void_p', # Opaque struct, do not mess with it.
        'libvlc_log_message_t*':       'ctypes.POINTER(LogMessage)',
        'libvlc_media_track_t**':      'ctypes.POINTER(MediaTrack)',
        'libvlc_media_track_t***':     'ctypes.POINTER(ctypes.POINTER(MediaTrack))',
        'libvlc_media_t*':             'Media',
        'libvlc_media_discoverer_t*':  'MediaDiscoverer',
        'libvlc_media_library_t*':     'MediaLibrary',
        'libvlc_media_list_t*':        'MediaList',
        'libvlc_media_list_player_t*': 'MediaListPlayer',
        'libvlc_media_list_view_t*':   'MediaListView',
        'libvlc_media_player_t*':      'MediaPlayer',
        'libvlc_media_stats_t*':       'ctypes.POINTER(MediaStats)',
        'libvlc_media_track_info_t**': 'ctypes.POINTER(ctypes.c_void_p)',
        'libvlc_rectangle_t*':         'ctypes.POINTER(Rectangle)',
        'libvlc_time_t':               'ctypes.c_longlong',
        'libvlc_track_description_t*': 'ctypes.POINTER(TrackDescription)',
        'libvlc_module_description_t*': 'ctypes.POINTER(ModuleDescription)',
        'libvlc_audio_output_device_t*': 'ctypes.POINTER(AudioOutputDevice)',

        'FILE*':                       'FILE_ptr',

        '...':       'ctypes.c_void_p',
        'va_list':   'ctypes.c_void_p',
        'char*':     'ctypes.c_char_p',
        'bool':      'ctypes.c_bool',
        'char**':    'ListPOINTER(ctypes.c_char_p)',
        'float':     'ctypes.c_float',
        'int':       'ctypes.c_int',
        'int*':      'ctypes.POINTER(ctypes.c_int)',  # _video_get_cursor
        'uintptr_t*':      'ctypes.POINTER(ctypes.c_uint)',
        'int64_t':   'ctypes.c_int64',
        'short':     'ctypes.c_short',
        'uint32_t':  'ctypes.c_uint32',
        'unsigned':  'ctypes.c_uint',
        'unsigned*': 'ctypes.POINTER(ctypes.c_uint)',  # _video_get_size
        'void':      'None',
        'void*':     'ctypes.c_void_p',
        'void**':    'ListPOINTER(ctypes.c_void_p)',

        'WINDOWHANDLE': 'ctypes.c_ulong',
    }

    # Python classes, i.e. classes for which we want to
    # generate class wrappers around libvlc functions
    defined_classes = (
        'EventManager',
        'Instance',
        'Log',
        'LogIterator',
        'Media',
        'MediaDiscoverer',
        'MediaLibrary',
        'MediaList',
        'MediaListPlayer',
        'MediaListView',
        'MediaPlayer',
    )

    def __init__(self, parser=None):
        """New instance.

        @param parser: a L{Parser} instance.
        """
        _Generator.__init__(self, parser)
        # one special enum type class
        self.type2class['libvlc_event_e'] = 'EventType'
        # doc links to functions, methods and types
        self.links = {'libvlc_event_e': 'EventType'}
        # link enum value names to enum type/class
##      for t in self.parser.enums:
##          for v in t.vals:
##              self.links[v.enum] = t.name
        # prefixes to strip from method names
        # when wrapping them into class methods
        self.prefixes = {}
        for t, c in self.type2class.items():
            t = t.rstrip('*')
            if c in self.defined_classes:
                self.links[t] = c
                self.prefixes[c] = t[:-1]
            elif c.startswith('ctypes.POINTER('):
                c = c.replace('ctypes.POINTER(', '') \
                     .rstrip(')')
                if c[:1].isupper():
                    self.links[t] = c
        # xform docs to epydoc lines
        for f in self.parser.funcs:
            f.xform()
            self.links[f.name] = f.name
        self.check_types()

    def generate_ctypes(self):
        """Generate a ctypes decorator for all functions.
        """
        self.output("""
 # LibVLC __version__ functions #
""")
        for f in self.parser.funcs:
            name = f.name  #PYCHOK flake

            # arg names, excluding output args
            args = ', '.join(f.args())  #PYCHOK flake

            # tuples of arg flags
            flags = ', '.join(str(p.flags(f.out)) for p in f.pars)  #PYCHOK false?
            if flags:
                flags += ','

            # arg classes
            types = [self.class4(p.type) for p in f.pars]

            # result type
            rtype = self.class4(f.type)

            if name in free_string_funcs:
                # some functions that return strings need special treatment
                if rtype != 'ctypes.c_char_p':
                    raise TypeError('Function %s expected to return char* not %s' % (name, f.type))
                errcheck = 'string_result'
                types = ['ctypes.c_void_p'] + types
            elif rtype in self.defined_classes:
                # if the result is a pointer to one of the defined
                # classes then we tell ctypes that the return type is
                # ctypes.c_void_p so that 64-bit pointers are handled
                # correctly, and then create a Python object of the
                # result
                errcheck = 'class_result(%s)' % rtype
                types = [ 'ctypes.c_void_p'] + types
            else:
                errcheck = 'None'
                types.insert(0, rtype)

            types = ', '.join(types)

            # xformed doc string with first @param
            docs = self.epylink(f.epydocs(0, 4))  #PYCHOK flake
            self.output("""def %(name)s(%(args)s):
    '''%(docs)s
    '''
    f = _Cfunctions.get('%(name)s', None) or \\
        _Cfunction('%(name)s', (%(flags)s), %(errcheck)s,
                    %(types)s)
    return f(%(args)s)
""" % locals())

    def generate_enums(self):
        """Generate classes for all enum types.
        """
        self.output("""
class _Enum(ctypes.c_uint):
    '''(INTERNAL) Base class
    '''
    _enum_names_ = {}

    def __str__(self):
        n = self._enum_names_.get(self.value, '') or ('FIXME_(%r)' % (self.value,))
        return '.'.join((self.__class__.__name__, n))

    def __repr__(self):
        return '.'.join((self.__class__.__module__, self.__str__()))

    def __eq__(self, other):
        return ( (isinstance(other, _Enum) and self.value == other.value)
              or (isinstance(other, _Ints) and self.value == other) )

    def __ne__(self, other):
        return not self.__eq__(other)
""")
        for e in self.parser.enums:

            cls = self.class4(e.name)
            self.output("""class %s(_Enum):
    '''%s
    '''
    _enum_names_ = {""" % (cls, e.epydocs() or _NA_))

            for v in e.vals:
                self.output("        %s: '%s'," % (v.value, v.name))
            self.output('    }')

            # align on '=' signs
            w = -max(len(v.name) for v in e.vals)
            t = ['%s.%*s = %s(%s)' % (cls, w,v.name, cls, v.value) for v in e.vals]

            self.output(_NL_.join(sorted(t)), nt=2)

    def generate_callbacks(self):
        """Generate decorators for callback functions.

        We generate both decorators (for defining functions) and
        associated classes, to help in defining function signatures.
        """
        if not self.parser.callbacks:
            return
        # Generate classes
        for f in self.parser.callbacks:
            name = self.class4(f.name)  #PYCHOK flake
            docs = self.epylink(f.docs)
            self.output('''class %(name)s(ctypes.c_void_p):
    """%(docs)s
    """
    pass''' % locals())

        self.output("class CallbackDecorators(object):")
        self.output('    "Class holding various method decorators for callback functions."')
        for f in self.parser.callbacks:
            name = self.class4(f.name)  #PYCHOK flake

            # return value and arg classes
            # Note: The f.type != 'void**' is a hack to generate a
            # valid ctypes signature, specifically for the
            # libvlc_video_lock_cb callback. It should be fixed in a better way (more generic)
            types = ', '.join([self.class4(f.type if f.type != 'void**' else 'void*')] +  #PYCHOK flake
                              [self.class4(p.type) for p in f.pars])

            # xformed doc string with first @param
            docs = self.epylink(f.docs)

            self.output("""    %(name)s = ctypes.CFUNCTYPE(%(types)s)
    %(name)s.__doc__ = '''%(docs)s
    ''' """ % locals())
        self.output("cb = CallbackDecorators")

    def generate_wrappers(self):
        """Generate class wrappers for all appropriate functions.
        """
        def striprefix(name):
            return name.replace(x, '').replace('libvlc_', '')

        codes, methods, docstrs = self.parse_override('override.py')

        # sort functions on the type/class
        # of their first parameter
        t = []
        for f in self.parser.funcs:
             if f.pars:
                 p = f.pars[0]
                 c = self.class4(p.type)
                 if c in self.defined_classes:
                     t.append((c, f))
        cls = x = ''  # wrap functions in class methods
        for c, f in sorted(t, key=operator.itemgetter(0)):
            if cls != c:
                cls = c
                self.output("""class %s(_Ctype):
    '''%s
    '''""" % (cls, docstrs.get(cls, '') or _NA_)) # """ emacs-mode is confused...

                c = codes.get(cls, '')
                if not 'def __new__' in c:
                    self.output("""
    def __new__(cls, ptr=_internal_guard):
        '''(INTERNAL) ctypes wrapper constructor.
        '''
        return _Constructor(cls, ptr)""")

                if c:
                    self.output(c)
                x = self.prefixes.get(cls, 'libvlc_')

            f.wrapped += 1
            name = f.name

            # method name is function name less prefix
            meth = striprefix(name)
            if meth in methods.get(cls, []):
                continue  # overridden

            # arg names, excluding output args
            # and rename first arg to 'self'
            args = ', '.join(['self'] + f.args(1))  #PYCHOK flake "
            wrapped_args = ', '.join(['self'] + [ ('str_to_bytes(%s)' % p.name
                                                   if p.type == 'char*'
                                                   else p.name)
                                                  for p in f.in_params(1) ])  #PYCHOK flake

            # xformed doc string without first @param
            docs = self.epylink(f.epydocs(1, 8), striprefix)  #PYCHOK flake

            self.output("""    def %(meth)s(%(args)s):
        '''%(docs)s
        '''
        return %(name)s(%(wrapped_args)s)
""" % locals())

            # check for some standard methods
            if meth == 'count':
                # has a count method, generate __len__
                self.output("""    def __len__(self):
        return %s(self)
""" % (name,))
            elif meth.endswith('item_at_index'):
                # indexable (and thus iterable)
                self.output("""    def __getitem__(self, i):
        return %s(self, i)

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]
""" % (name,))

    def parse_override(self, override):
        """Parse the override definitions file.

        It is possible to override methods definitions in classes.

        @param override: the C{override.py} file name.

        @return: a tuple (codes, methods, docstrs) of 3 dicts
        containing the source code, the method names and the
        class-level doc strings for each of the classes defined
        in the B{override} file.
        """
        codes = {}
        k, v = None, []
        f = opener(override)
        for t in f:
            m = class_re.match(t)
            if m:  # new class
                if k is not None:
                    codes[k] = ''.join(v)
                k, v = m.group(1), []
            else:
                v.append(t)
        if k is not None:
            codes[k] = ''.join(v)
        f.close()

        docstrs, methods = {}, {}
        for k, v in codes.items():
            q = v.lstrip()[:3]
            if q in ('"""', "'''"):
                # use class comment as doc string
                _, docstrs[k], v = v.split(q, 2)
                codes[k] = v
            # FIXME: not robust wrt. internal methods
            methods[k] = def_re.findall(v)

        return codes, methods, docstrs

    def save(self, path=None):
        """Write Python bindings to a file or C{stdout}.
        """
        self.outopen(path or '-')
        self.insert_code('header.py', genums=True)

        self.generate_wrappers()
        self.generate_ctypes()

        self.unwrapped()

        self.insert_code('footer.py')
        self.outclose()


class JavaGenerator(_Generator):
    """Generate Java/JNA bindings.
    """
    comment_line = '//'
    type_re      = re.compile('libvlc_(.+?)(_[te])?$')

    # C-type to Java/JNA type conversion.
    type2class = {
        'libvlc_audio_output_t*':      'LibVlcAudioOutput',
        'libvlc_callback_t':           'LibVlcCallback',
        'libvlc_event_type_t':         'LibvlcEventType',
        'libvlc_event_manager_t*':     'LibVlcEventManager',
        'libvlc_instance_t*':          'LibVlcInstance',
        'libvlc_log_t*':               'LibVlcLog',
        'libvlc_log_iterator_t*':      'LibVlcLogIterator',
        'libvlc_log_message_t*':       'LibvlcLogMessage',
        'libvlc_media_t*':             'LibVlcMedia',
        'libvlc_media_discoverer_t*':  'LibVlcMediaDiscoverer',
        'libvlc_media_library_t*':     'LibVlcMediaLibrary',
        'libvlc_media_list_t*':        'LibVlcMediaList',
        'libvlc_media_list_player_t*': 'LibVlcMediaListPlayer',
        'libvlc_media_list_view_t*':   'LibVlcMediaListView',
        'libvlc_media_player_t*':      'LibVlcMediaPlayer',
        'libvlc_media_stats_t*':       'LibVlcMediaStats',
        'libvlc_media_track_info_t**': 'LibVlcMediaTrackInfo',
        'libvlc_time_t':               'long',
        'libvlc_track_description_t*': 'LibVlcTrackDescription',

        '...':       'FIXME_va_list',
        'char*':     'String',
        'char**':    'String[]',
        'float':     'float',
        'int':       'int',
        'int*':      'Pointer',
        'int64_t':   'long',
        'short':     'short',
        'uint32_t':  'uint32',
        'unsigned':  'int',
        'unsigned*': 'Pointer',
        'void':      'void',
        'void*':     'Pointer',
    }

    def __init__(self, parser=None):
        """New instance.

        @param parser: a L{Parser} instance.
        """
        _Generator.__init__(self, parser)
        self.check_types()

    def generate_enums(self):
        """Generate Java/JNA glue code for enums.
        """
        for e in self.parser.enums:

            j = self.class4(e.name)
            self.outopen(j + '.java')

            self.insert_code('boilerplate.java')
            self.output("""package org.videolan.jvlc.internal;

public enum %s
{""" % (j,))
            # FIXME: write comment
            for v in e.vals:
                self.output('        %s (%s),' % (v.name, v.value))
            self.output("""
        private final int _value;
        %s(int value) { this._value = value; }
        public int value() { return this._value; }
}""" % (j,))
            self.outclose()

    def generate_header(self):
        """Generate LibVlc header.
        """
        for c, j in sorted(self.type2class.items()):
            if c.endswith('*') and j.startswith('LibVlc'):
                self.output("""
    public class %s extends PointerType
    {
    }""" % (j,))

    def generate_libvlc(self):
        """Generate LibVlc.java Java/JNA glue code.
        """
        self.outopen('LibVlc.java')

        self.insert_code('boilerplate.java')
        self.insert_code('LibVlc-header.java')

        self.generate_header()
        for f in self.parser.funcs:
            f.wrapped = 1  # for now
            p =    ', '.join('%s %s' % (self.class4(p.type), p.name) for p in f.pars)
            self.output('%s %s(%s);' % (self.class4(f.type), f.name, p), nt=2)

        self.insert_code('LibVlc-footer.java')

        self.unwrapped()
        self.outclose()

    def save(self, dir=None):
        """Write Java bindings into the given directory.
        """
        if dir in (None, '-'):
            d = 'internal'
            if not os.path.isdir(d):
                os.makedirs(d)  # os.mkdir(d)
        else:
            d = dir or os.curdir
        self.outdir = d

        sys.stderr.write('Generating Java code in %s...\n' % os.path.join(d, ''))

        self.generate_enums()
        self.generate_libvlc()


def process(output, h_files):
    """Generate Python bindings.
    """
    p = Parser(h_files)
    g = PythonGenerator(p)
    g.save(output)


if __name__ == '__main__':

    from optparse import OptionParser

    opt = OptionParser(usage="""%prog  [options]  <include_vlc_directory> | <include_file.h> [...]

Parse VLC include files and generate bindings code for Python or Java.""")

    opt.add_option('-c', '--check', dest='check', action='store_true',
                   default=False,
                   help='Check mode, generates no bindings')

    opt.add_option('-d', '--debug', dest='debug', action='store_true',
                   default=False,
                   help='Debug mode, generate no bindings')

    opt.add_option('-j', '--java', dest='java', action='store_true',
                   default=False,
                   help='Generate Java bindings (default is Python)')

    opt.add_option('-o', '--output', dest='output', action='store', type='str',
                   default='-',
                   help='Output filename (for Python) or directory (for Java)')

    opt.add_option('-v', '--version', dest='version', action='store', type='str',
                   default='',
                   help='Version string for __version__ global')

    opts, args = opt.parse_args()

    if '--debug' in sys.argv:
       _debug = True  # show source

    if not args:
        opt.print_help()
        sys.exit(1)

    elif len(args) == 1:  # get .h files
        # get .h files from .../include/vlc dir
        # or .../include/vlc/*.h (especially
        # useful on Windows, where cmd does
        # not provide wildcard expansion)
        p = args[0]
        if os.path.isdir(p):
            p = os.path.join(p, '*.h')
        import glob
        args = glob.glob(p)

    p = Parser(args, opts.version)
    if opts.debug:
        for t in ('enums', 'funcs', 'callbacks'):
            p.dump(t)

    if opts.java:
        g = JavaGenerator(p)
    else:
        g = PythonGenerator(p)

    if opts.check:
        p.check()
    elif opts.debug:
        g.dump_dicts()
    elif not _nerrors:
        g.save(opts.output)

    errors('%s error(s) reported')
