#!/usr/bin/env python
# Script to analyze code and arrange ld sections.
#
# Copyright (C) 2008  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import sys

# LD script headers/trailers
COMMONHEADER = """
/* DO NOT EDIT!  This is an autogenerated file.  See tools/layoutrom.py. */
OUTPUT_FORMAT("elf32-i386")
OUTPUT_ARCH("i386")
SECTIONS
{
"""
COMMONTRAILER = """

        /* Discard regular data sections to force a link error if
         * code attempts to access data not marked with VAR16 (or other
         * appropriate macro)
         */
        /DISCARD/ : {
                *(.text*) *(.data*) *(.bss*) *(.rodata*)
                *(COMMON) *(.discard*) *(.eh_frame)
                }
}
"""


######################################################################
# Determine section locations
######################################################################

# Align 'pos' to 'alignbytes' offset
def alignpos(pos, alignbytes):
    mask = alignbytes - 1
    return (pos + mask) & ~mask

# Determine the final addresses for a list of sections that end at an
# address.
def getSectionsStart(sections, endaddr, minalign=1):
    totspace = 0
    for size, align, name in sections:
        if align > minalign:
            minalign = align
        totspace = alignpos(totspace, align) + size
    startaddr = (endaddr - totspace) / minalign * minalign
    curaddr = startaddr
    # out = [(addr, sectioninfo), ...]
    out = []
    for sectioninfo in sections:
        size, align, name = sectioninfo
        curaddr = alignpos(curaddr, align)
        out.append((curaddr, sectioninfo))
        curaddr += size
    return out, startaddr

# Return the subset of sections with a given name prefix
def getSectionsPrefix(sections, prefix):
    lp = len(prefix)
    out = []
    for size, align, name in sections:
        if name[:lp] == prefix:
            out.append((size, align, name))
    return out

# The 16bit code can't exceed 64K of space.
BUILD_BIOS_ADDR = 0xf0000
BUILD_BIOS_SIZE = 0x10000

# Layout the 16bit code.  This ensures sections with fixed offset
# requirements are placed in the correct location.  It also places the
# 16bit code as high as possible in the f-segment.
def fitSections(sections, fillsections):
    canrelocate = list(fillsections)
    # fixedsections = [(addr, sectioninfo), ...]
    fixedsections = []
    for sectioninfo in sections:
        size, align, name = sectioninfo
        if name[:11] == '.fixedaddr.':
            addr = int(name[11:], 16)
            fixedsections.append((addr, sectioninfo))
            if align != 1:
                print "Error: Fixed section %s has non-zero alignment (%d)" % (
                    name, align)
                sys.exit(1)

    # Find freespace in fixed address area
    fixedsections.sort()
    # fixedAddr = [(freespace, sectioninfo), ...]
    fixedAddr = []
    for i in range(len(fixedsections)):
        fixedsectioninfo = fixedsections[i]
        addr, section = fixedsectioninfo
        if i == len(fixedsections) - 1:
            nextaddr = BUILD_BIOS_SIZE
        else:
            nextaddr = fixedsections[i+1][0]
        avail = nextaddr - addr - section[0]
        fixedAddr.append((avail, fixedsectioninfo))

    # Attempt to fit other sections into fixed area
    extrasections = []
    fixedAddr.sort()
    canrelocate.sort()
    totalused = 0
    for freespace, fixedsectioninfo in fixedAddr:
        fixedaddr, fixedsection = fixedsectioninfo
        addpos = fixedaddr + fixedsection[0]
        totalused += fixedsection[0]
        nextfixedaddr = addpos + freespace
#        print "Filling section %x uses %d, next=%x, available=%d" % (
#            fixedaddr, fixedsection[0], nextfixedaddr, freespace)
        while 1:
            canfit = None
            for fitsection in canrelocate:
                fitsize, fitalign, fitname = fitsection
                if addpos + fitsize > nextfixedaddr:
                    # Can't fit and nothing else will fit.
                    break
                fitnextaddr = alignpos(addpos, fitalign) + fitsize
#                print "Test %s - %x vs %x" % (
#                    fitname, fitnextaddr, nextfixedaddr)
                if fitnextaddr > nextfixedaddr:
                    # This item can't fit.
                    continue
                canfit = (fitnextaddr, fitsection)
            if canfit is None:
                break
            # Found a section that can fit.
            fitnextaddr, fitsection = canfit
            canrelocate.remove(fitsection)
            extrasections.append((addpos, fitsection))
            addpos = fitnextaddr
            totalused += fitsection[0]
#            print "    Adding %s (size %d align %d) pos=%x avail=%d" % (
#                fitsection[2], fitsection[0], fitsection[1]
#                , fitnextaddr, nextfixedaddr - fitnextaddr)
    firstfixed = fixedsections[0][0]

    # Report stats
    total = BUILD_BIOS_SIZE-firstfixed
    slack = total - totalused
    print ("Fixed space: 0x%x-0x%x  total: %d  slack: %d"
           "  Percent slack: %.1f%%" % (
            firstfixed, BUILD_BIOS_SIZE, total, slack,
            (float(slack) / total) * 100.0))

    return fixedsections + extrasections, firstfixed

def doLayout(sections16, sections32seg, sections32flat):
    # Determine 16bit positions
    textsections = getSectionsPrefix(sections16, '.text.')
    rodatasections = (getSectionsPrefix(sections16, '.rodata.str1.1')
                      + getSectionsPrefix(sections16, '.rodata.__func__.'))
    datasections = getSectionsPrefix(sections16, '.data16.')
    fixedsections = getSectionsPrefix(sections16, '.fixedaddr.')

    locs16fixed, firstfixed = fitSections(fixedsections, textsections)
    prunesections = [i[1] for i in locs16fixed]
    remsections = [i for i in textsections+rodatasections+datasections
                   if i not in prunesections]
    locs16, code16_start = getSectionsStart(remsections, firstfixed)
    locs16 = locs16 + locs16fixed
    locs16.sort()

    # Determine 32seg positions
    textsections = getSectionsPrefix(sections32seg, '.text.')
    rodatasections = (getSectionsPrefix(sections32seg, '.rodata.str1.1')
                      + getSectionsPrefix(sections32seg, '.rodata.__func__.'))
    datasections = getSectionsPrefix(sections32seg, '.data32seg.')

    locs32seg, code32seg_start = getSectionsStart(
        textsections + rodatasections + datasections, code16_start)

    # Determine 32flat positions
    textsections = getSectionsPrefix(sections32flat, '.text.')
    rodatasections = getSectionsPrefix(sections32flat, '.rodata')
    datasections = getSectionsPrefix(sections32flat, '.data.')
    bsssections = getSectionsPrefix(sections32flat, '.bss.')

    locs32flat, code32flat_start = getSectionsStart(
        textsections + rodatasections + datasections + bsssections
        , code32seg_start + BUILD_BIOS_ADDR, 16)

    # Print statistics
    size16 = BUILD_BIOS_SIZE - code16_start
    size32seg = code16_start - code32seg_start
    size32flat = code32seg_start + BUILD_BIOS_ADDR - code32flat_start
    print "16bit size:           %d" % size16
    print "32bit segmented size: %d" % size32seg
    print "32bit flat size:      %d" % size32flat

    return locs16, locs32seg, locs32flat


######################################################################
# Linker script output
######################################################################

# Write LD script includes for the given cross references
def outXRefs(xrefs, finallocs, delta=0):
    out = ""
    for symbol, (fileid, section, addr) in xrefs.items():
        if fileid < 2:
            addr += delta
        out += "%s = 0x%x ;\n" % (symbol, finallocs[(fileid, section)] + addr)
    return out

# Write LD script includes for the given sections using relative offsets
def outRelSections(locs, startsym):
    out = ""
    for addr, sectioninfo in locs:
        size, align, name = sectioninfo
        out += ". = ( 0x%x - %s ) ;\n" % (addr, startsym)
        if name == '.rodata.str1.1':
            out += "_rodata = . ;\n"
        out += "*(%s)\n" % (name,)
    return out

# Layout the 32bit segmented code.  This places the code as high as possible.
def writeLinkerScripts(locs16, locs32seg, locs32flat
                       , xref16, xref32seg, xref32flat
                       , out16, out32seg, out32flat):
    # Index to final location for each section
    # finallocs[(fileid, section)] = addr
    finallocs = {}
    for fileid, locs in ((0, locs16), (1, locs32seg), (2, locs32flat)):
        for addr, sectioninfo in locs:
            finallocs[(fileid, sectioninfo[2])] = addr

    # Write 16bit linker script
    code16_start = locs16[0][0]
    output = open(out16, 'wb')
    output.write(COMMONHEADER + outXRefs(xref16, finallocs) + """
    code16_start = 0x%x ;
    .text16 code16_start : {
""" % (code16_start)
                 + outRelSections(locs16, 'code16_start')
                 + """
    }
"""
                 + COMMONTRAILER)
    output.close()

    # Write 32seg linker script
    code32seg_start = code16_start
    if locs32seg:
        code32seg_start = locs32seg[0][0]
    output = open(out32seg, 'wb')
    output.write(COMMONHEADER + outXRefs(xref32seg, finallocs) + """
    code32seg_start = 0x%x ;
    .text32seg code32seg_start : {
""" % (code32seg_start)
                 + outRelSections(locs32seg, 'code32seg_start')
                 + """
    }
"""
                 + COMMONTRAILER)
    output.close()

    # Write 32flat linker script
    output = open(out32flat, 'wb')
    output.write(COMMONHEADER
                 + outXRefs(xref32flat, finallocs, BUILD_BIOS_ADDR) + """
    code32flat_start = 0x%x ;
    .text code32flat_start : {
""" % (locs32flat[0][0])
                 + outRelSections(locs32flat, 'code32flat_start')
                 + """
        . = ( 0x%x - code32flat_start ) ;
        *(.text32seg)
        . = ( 0x%x - code32flat_start ) ;
        *(.text16)
        code32flat_end = ABSOLUTE(.) ;
    } :text
""" % (code32seg_start + BUILD_BIOS_ADDR, code16_start + BUILD_BIOS_ADDR)
                 + COMMONTRAILER
                 + """
ENTRY(post32)
PHDRS
{
        text PT_LOAD AT ( code32flat_start ) ;
}
""")
    output.close()


######################################################################
# Section garbage collection
######################################################################

# Find and keep the section associated with a symbol (if available).
def keepsymbol(symbol, infos, pos, callerpos=None):
    addr, section = infos[pos][1].get(symbol, (None, None))
    if section is None or '*' in section or section[:9] == '.discard.':
        return -1
    if callerpos is not None and symbol not in infos[callerpos][4]:
        # This symbol reference is a cross section reference (an xref).
        # xref[symbol] = (fileid, section, addr)
        infos[callerpos][4][symbol] = (pos, section, addr)
    keepsection(section, infos, pos)
    return 0

# Note required section, and recursively set all referenced sections
# as required.
def keepsection(name, infos, pos=0):
    if name in infos[pos][3]:
        # Already kept - nothing to do.
        return
    infos[pos][3].append(name)
    relocs = infos[pos][2].get(name)
    if relocs is None:
        return
    # Keep all sections that this section points to
    for symbol in relocs:
        ret = keepsymbol(symbol, infos, pos)
        if not ret:
            continue
        # Not in primary sections - it may be a cross 16/32 reference
        ret = keepsymbol(symbol, infos, (pos+1)%3, pos)
        if not ret:
            continue
        ret = keepsymbol(symbol, infos, (pos+2)%3, pos)
        if not ret:
            continue

# Return a list of kept sections.
def getSectionsList(sections, names):
    return [i for i in sections if i[2] in names]

# Determine which sections are actually referenced and need to be
# placed into the output file.
def gc(info16, info32seg, info32flat):
    # infos = ((sections, symbols, relocs, keep sections, xrefs), ...)
    infos = ((info16[0], info16[1], info16[2], [], {}),
             (info32seg[0], info32seg[1], info32seg[2], [], {}),
             (info32flat[0], info32flat[1], info32flat[2], [], {}))
    # Start by keeping sections that are globally visible.
    for size, align, section in info16[0]:
        if section[:11] == '.fixedaddr.' or '.export.' in section:
            keepsection(section, infos)
    keepsymbol('post32', infos, 0, 2)
    # Return sections found.
    keep16 = getSectionsList(info16[0], infos[0][3]), infos[0][4]
    keep32seg = getSectionsList(info32seg[0], infos[1][3]), infos[1][4]
    keep32flat = getSectionsList(info32flat[0], infos[2][3]), infos[2][4]
    return keep16, keep32seg, keep32flat


######################################################################
# Startup and input parsing
######################################################################

# Read in output from objdump
def parseObjDump(file):
    # sections = [(size, align, section), ...]
    sections = []
    # symbols[symbol] = (addr, section)
    symbols = {}
    # relocs[section] = [symbol, ...]
    relocs = {}

    state = None
    for line in file.readlines():
        line = line.rstrip()
        if line == 'Sections:':
            state = 'section'
            continue
        if line == 'SYMBOL TABLE:':
            state = 'symbol'
            continue
        if line[:24] == 'RELOCATION RECORDS FOR [':
            state = 'reloc'
            relocsection = line[24:-2]
            continue

        if state == 'section':
            try:
                idx, name, size, vma, lma, fileoff, align = line.split()
                if align[:3] != '2**':
                    continue
                sections.append((int(size, 16), 2**int(align[3:]), name))
            except:
                pass
            continue
        if state == 'symbol':
            try:
                section, size, symbol = line[17:].split()
                size = int(size, 16)
                addr = int(line[:8], 16)
                symbols[symbol] = addr, section
            except:
                pass
            continue
        if state == 'reloc':
            try:
                off, type, symbol = line.split()
                off = int(off, 16)
                relocs.setdefault(relocsection, []).append(symbol)
            except:
                pass
    return sections, symbols, relocs

def main():
    # Get output name
    in16, in32seg, in32flat, out16, out32seg, out32flat = sys.argv[1:]

    # Read in the objdump information
    infile16 = open(in16, 'rb')
    infile32seg = open(in32seg, 'rb')
    infile32flat = open(in32flat, 'rb')

    # infoX = (sections, symbols, relocs)
    info16 = parseObjDump(infile16)
    info32seg = parseObjDump(infile32seg)
    info32flat = parseObjDump(infile32flat)

    # Figure out which sections to keep.
    # keepX = (sections, xrefs)
    keep16, keep32seg, keep32flat = gc(info16, info32seg, info32flat)

    # Determine the final memory locations of each kept section.
    # locsX = [(addr, sectioninfo), ...]
    locs16, locs32seg, locs32flat = doLayout(
        keep16[0], keep32seg[0], keep32flat[0])

    # Write out linker script files.
    writeLinkerScripts(locs16, locs32seg, locs32flat
                       , keep16[1], keep32seg[1], keep32flat[1]
                       , out16, out32seg, out32flat)

if __name__ == '__main__':
    main()
