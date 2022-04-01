#!/usr/bin/env python3
#coding=utf-8

"""
AMI UCP Extract
AMI UCP BIOS Extractor
Copyright (C) 2021-2022 Plato Mavropoulos
"""

title = 'AMI UCP BIOS Extractor v2.0_a1'

import os
import sys
import shutil
import struct
import ctypes
import contextlib

# Stop __pycache__ generation
sys.dont_write_bytecode = True

from common.patterns import PAT_AMI_UCP, PAT_INTEL_ENG
from common.checksums import checksum16
from common.text_ops import padder
from common.a7z_comp import a7z_decompress, is_7z_supported
from common.efi_comp import efi_decompress, is_efi_compressed
from common.path_ops import argparse_init, process_input_files, safe_name
from common.struct_ops import get_struct, char, uint8_t, uint16_t, uint32_t
from common.system import nice_exc_handler, check_sys_py, check_sys_os, show_title, print_input
from AMI_PFAT_Extract import get_ami_pfat, parse_pfat_file

class UafHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('ModuleTag',       char*4),        # 0x00
        ('ModuleSize',      uint32_t),      # 0x04
        ('Checksum',        uint16_t),      # 0x08
        ('Unknown0',        uint8_t),       # 0x0A
        ('Unknown1',        uint8_t),       # 0x0A
        ('Reserved',        uint32_t),      # 0x0C
        # 0x10
    ]
    
    def struct_print(self, padding):
        p = padder(padding)
        
        print(p + 'Tag          :', self.ModuleTag.decode('utf-8'))
        print(p + 'Size         :', '0x%X' % self.ModuleSize)
        print(p + 'Checksum     :', '0x%0.4X' % self.Checksum)
        print(p + 'Unknown 0    :', '0x%0.2X' % self.Unknown0)
        print(p + 'Unknown 1    :', '0x%0.2X' % self.Unknown1)
        print(p + 'Reserved     :', '0x%0.8X' % self.Reserved)

class UafModule(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('CompressSize',    uint32_t),      # 0x00
        ('OriginalSize',    uint32_t),      # 0x04
        # 0x08
    ]
    
    def struct_print(self, padding, filename):
        p = padder(padding)
        
        print(p + 'Compress Size:', '0x%X' % self.CompressSize)
        print(p + 'Original Size:', '0x%X' % self.OriginalSize)
        print(p + 'File Name    :', filename)

class UiiHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('UIISize',         uint16_t),      # 0x00
        ('Checksum',        uint16_t),      # 0x02
        ('UtilityVersion',  uint32_t),      # 0x04 AFU|BGT (Unknown, Signed)
        ('InfoSize',        uint16_t),      # 0x08
        ('SupportBIOS',     uint8_t),       # 0x0A
        ('SupportOS',       uint8_t),       # 0x0B
        ('DataBusWidth',    uint8_t),       # 0x0C
        ('ProgramType',     uint8_t),       # 0x0D
        ('ProgramMode',     uint8_t),       # 0x0E
        ('SourceSafeRel',   uint8_t),       # 0x0F
        # 0x10
    ]
    
    SBI = {1: 'ALL', 2: 'AMIBIOS8', 3: 'UEFI', 4: 'AMIBIOS8/UEFI'}
    SOS = {1: 'DOS', 2: 'EFI', 3: 'Windows', 4: 'Linux', 5: 'FreeBSD', 6: 'MacOS', 128: 'Multi-Platform'}
    DBW = {1: '16b', 2: '16/32b', 3: '32b', 4: '64b'}
    PTP = {1: 'Executable', 2: 'Library', 3: 'Driver'}
    PMD = {1: 'API', 2: 'Console', 3: 'GUI', 4: 'Console/GUI'}
    
    def struct_print(self, padding, description):
        p = padder(padding)
        
        SupportBIOS = self.SBI.get(self.SupportBIOS, 'Unknown (%d)' % self.SupportBIOS)
        SupportOS = self.SOS.get(self.SupportOS, 'Unknown (%d)' % self.SupportOS)
        DataBusWidth = self.DBW.get(self.DataBusWidth, 'Unknown (%d)' % self.DataBusWidth)
        ProgramType = self.PTP.get(self.ProgramType, 'Unknown (%d)' % self.ProgramType)
        ProgramMode = self.PMD.get(self.ProgramMode, 'Unknown (%d)' % self.ProgramMode)
        
        print(p + 'UII Size      :', '0x%X' % self.UIISize)
        print(p + 'Checksum      :', '0x%0.4X' % self.Checksum)
        print(p + 'Tool Version  :', '0x%0.8X' % self.UtilityVersion)
        print(p + 'Info Size     :', '0x%X' % self.InfoSize)
        print(p + 'Supported BIOS:', SupportBIOS)
        print(p + 'Supported OS  :', SupportOS)
        print(p + 'Data Bus Width:', DataBusWidth)
        print(p + 'Program Type  :', ProgramType)
        print(p + 'Program Mode  :', ProgramMode)
        print(p + 'SourceSafe Tag:', '%0.2d' % self.SourceSafeRel)
        print(p + 'Description   :', description)

class DisHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('PasswordSize',    uint16_t),      # 0x00
        ('EntryCount',      uint16_t),      # 0x02
        ('Password',        char*12),       # 0x04
        # 0x10
    ]
    
    def struct_print(self, padding):
        p = padder(padding)
        
        print(p + 'Password Size:', '0x%X' % self.PasswordSize)
        print(p + 'Entry Count  :', self.EntryCount)
        print(p + 'Password     :', self.Password.decode('utf-8'))

class DisModule(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ('EnabledDisabled', uint8_t),       # 0x00
        ('ShownHidden',     uint8_t),       # 0x01
        ('Command',         char*32),       # 0x02
        ('Description',     char*256),      # 0x22
        # 0x122
    ]
    
    ENDIS = {0: 'Disabled', 1: 'Enabled'}
    SHOWN = {0: 'Hidden', 1: 'Shown', 2: 'Shown Only'}
    
    def struct_print(self, padding):
        p = padder(padding)
        
        EnabledDisabled = self.ENDIS.get(self.EnabledDisabled, 'Unknown (%d)' % self.EnabledDisabled)
        ShownHidden = self.SHOWN.get(self.ShownHidden, 'Unknown (%d)' % self.ShownHidden)
        
        print(p + 'State      :', EnabledDisabled)
        print(p + 'Display    :', ShownHidden)
        print(p + 'Command    :', self.Command.decode('utf-8').strip())
        print(p + 'Description:', self.Description.decode('utf-8').strip())

# Validate @UAF Module Checksum-16
def chk16_validate(data, tag, padd=0):
    if checksum16(data) != 0:
        print_input('\n%sError: Invalid UCP Module %s Checksum!' % (padder(padd), tag))
    else:
        print('\n%sChecksum of UCP Module %s is valid!' % (padder(padd), tag))

# Get all input file AMI UCP patterns
def get_ami_ucp(buffer):
    uaf_len_max = 0x0 # Length of largest detected @UAF
    uaf_hdr_off = 0x0 # Offset of largest detected @UAF
    uaf_buf_bin = b'' # Buffer of largest detected @UAF
    
    for uaf in PAT_AMI_UCP.finditer(buffer):
        uaf_len_cur = int.from_bytes(buffer[uaf.start() + 0x4:uaf.start() + 0x8], 'little')
        
        if uaf_len_cur > uaf_len_max:
            uaf_len_max = uaf_len_cur
            uaf_hdr_off = uaf.start()
            uaf_buf_bin = buffer[uaf_hdr_off:uaf_hdr_off + uaf_len_max]
    
    return uaf_hdr_off, uaf_buf_bin

# Get list of @UAF Modules
def get_uaf_mod(buffer, uaf_off=0x0):
    uaf_all = [] # Initialize list of all @UAF Modules
    
    while buffer[uaf_off] == 0x40: # ASCII of @ is 0x40
        uaf_hdr = get_struct(buffer, uaf_off, UafHeader) # Parse @UAF Module Structure
        
        uaf_tag = uaf_hdr.ModuleTag.decode('utf-8') # Get unique @UAF Module Tag
        
        uaf_all.append([uaf_tag, uaf_off, uaf_hdr]) # Store @UAF Module Info
        
        uaf_off += uaf_hdr.ModuleSize # Adjust to next @UAF Module offset
        
        if uaf_off >= len(buffer): break # Stop parsing at EOF
    
    # Check if @UAF Module NAL exists and place it first
    # Parsing NAL first allows naming all @UAF Modules
    for mod_idx,mod_val in enumerate(uaf_all):
        if mod_val[0] == '@NAL':
            uaf_all.insert(1, uaf_all.pop(mod_idx)) # After UII for visual purposes
            break # NAL found, skip the rest
    
    return uaf_all

# Parse & Extract AMI UCP structures
def ucp_extract(buffer, output_path, padding=0, is_chk16=False):
    nal_dict = {} # Initialize @NAL Dictionary per UCP
    
    print('\n%sUtility Configuration Program' % padder(padding))
    
    extract_path = os.path.join(output_path + '_extracted', '')
    
    if os.path.isdir(extract_path): shutil.rmtree(extract_path)
    
    os.mkdir(extract_path)
    
    uaf_hdr = get_struct(buffer, 0, UafHeader) # Parse @UAF Header Structure
    
    print('\n%sUtility Auxiliary File > @UAF:\n' % padder(padding + 4))
    
    uaf_hdr.struct_print(padding + 8)
    
    fake = struct.pack('<II', len(buffer), len(buffer)) # Generate UafModule Structure
    
    uaf_mod = get_struct(fake, 0x0, UafModule) # Parse UAF Module EFI Structure
    
    uaf_mod.struct_print(padding + 8, UAF_TAG_DICT['@UAF'][0]) # Print @UAF Module EFI Info
    
    if is_chk16: chk16_validate(buffer, '@UAF', padding + 8)
    
    uaf_all = get_uaf_mod(buffer, UAF_HDR_LEN)
    
    for mod_info in uaf_all:
        nal_dict = uaf_extract(buffer, extract_path, mod_info, padding + 8, is_chk16, nal_dict)

# Parse & Extract AMI UCP > @UAF Module/Section
def uaf_extract(buffer, extract_path, mod_info, padding=0, is_chk16=False, nal_dict=None):
    if nal_dict is None: nal_dict = {}
    
    uaf_tag,uaf_off,uaf_hdr = mod_info
    
    uaf_data_all = buffer[uaf_off:uaf_off + uaf_hdr.ModuleSize] # @UAF Module Entire Data
    
    uaf_data_mod = uaf_data_all[UAF_HDR_LEN:] # @UAF Module EFI Data
    
    uaf_data_raw = uaf_data_mod[UAF_MOD_LEN:] # @UAF Module Raw Data
    
    print('\n%sUtility Auxiliary File > %s:\n' % (padder(padding), uaf_tag))
    
    uaf_hdr.struct_print(padding + 4) # Print @UAF Module Info
    
    uaf_mod = get_struct(buffer, uaf_off + UAF_HDR_LEN, UafModule) # Parse UAF Module EFI Structure
    
    is_comp = uaf_mod.CompressSize != uaf_mod.OriginalSize # Detect @UAF Module EFI Compression
    
    if uaf_tag in nal_dict: uaf_name = nal_dict[uaf_tag] # Always prefer NAL naming first
    elif uaf_tag in UAF_TAG_DICT: uaf_name = UAF_TAG_DICT[uaf_tag][0] # Otherwise use built-in naming
    elif uaf_tag == '@ROM': uaf_name = 'BIOS.bin' # BIOS/PFAT Firmware (w/o Signature)
    elif uaf_tag.startswith('@R0'): uaf_name = 'BIOS_0%s.bin' % uaf_tag[3:] # BIOS/PFAT Firmware
    elif uaf_tag.startswith('@S0'): uaf_name = 'BIOS_0%s.sig' % uaf_tag[3:] # BIOS/PFAT Signature
    elif uaf_tag.startswith('@DR'): uaf_name = 'DROM_0%s.bin' % uaf_tag[3:] # Thunderbolt Retimer Firmware
    elif uaf_tag.startswith('@DS'): uaf_name = 'DROM_0%s.sig' % uaf_tag[3:] # Thunderbolt Retimer Signature
    elif uaf_tag.startswith('@EC'): uaf_name = 'EC_0%s.bin' % uaf_tag[3:] # Embedded Controller Firmware
    elif uaf_tag.startswith('@ME'): uaf_name = 'ME_0%s.bin' % uaf_tag[3:] # Management Engine Firmware
    else: uaf_name = uaf_tag # Could not name the @UAF Module, use Tag instead
    
    uaf_fext = '' if uaf_name != uaf_tag else '.bin'
    
    uaf_mod.struct_print(padding + 4, uaf_name + uaf_fext) # Print @UAF Module EFI Info
    
    # Check if unknown @UAF Module Tag is present in NAL but not in built-in dictionary
    if uaf_tag in nal_dict and uaf_tag not in UAF_TAG_DICT and not uaf_tag.startswith(('@ROM','@R0','@S0','@DR','@DS')):
        print_input('\n%sNote: Detected new AMI UCP Module %s (%s) in NAL!' % (padder(padding), uaf_tag, nal_dict[uaf_tag]))
    
    # Generate @UAF Module File name, depending on whether decompression will be required
    uaf_fname = os.path.join(extract_path, safe_name(uaf_name + ('.temp' if is_comp else uaf_fext)))
    
    if is_chk16: chk16_validate(uaf_data_all, uaf_tag, padding + 4)
    
    # Parse Utility Identification Information @UAF Module (@UII)
    if uaf_tag == '@UII':
        info_hdr = get_struct(uaf_data_raw, 0, UiiHeader) # Parse @UII Module Raw Structure
        
        info_data = uaf_data_raw[max(UII_HDR_LEN,info_hdr.InfoSize):info_hdr.UIISize] # @UII Module Info Data
        
        # Get @UII Module Info/Description text field
        info_desc = info_data.decode('utf-8','ignore').strip('\x00 ')
        
        print('\n%sUtility Identification Information:\n' % padder(padding + 4))
        
        info_hdr.struct_print(padding + 8, info_desc) # Print @UII Module Info
        
        if is_chk16: chk16_validate(uaf_data_raw, '@UII > Info', padding + 8)
        
        # Store/Save @UII Module Info in file
        with open(uaf_fname[:-4] + '.txt', 'a', encoding='utf-8') as uii_out:
            with contextlib.redirect_stdout(uii_out):
                info_hdr.struct_print(0, info_desc) # Store @UII Module Info
    
    # Adjust @UAF Module Raw Data for extraction
    if is_comp:
        # Some Compressed @UAF Module EFI data lack necessary EOF padding
        if uaf_mod.CompressSize > len(uaf_data_raw):
            comp_padd = b'\x00' * (uaf_mod.CompressSize - len(uaf_data_raw))
            uaf_data_raw = uaf_data_mod[:UAF_MOD_LEN] + uaf_data_raw + comp_padd # Add missing padding for decompression
        else:
            uaf_data_raw = uaf_data_mod[:UAF_MOD_LEN] + uaf_data_raw # Add the EFI/Tiano Compression info before Raw Data
    else:
        uaf_data_raw = uaf_data_raw[:uaf_mod.OriginalSize] # No compression, extend to end of Original @UAF Module size
    
    # Store/Save @UAF Module file
    if uaf_tag != '@UII': # Skip @UII binary, already parsed
        with open(uaf_fname, 'wb') as uaf_out: uaf_out.write(uaf_data_raw)
    
    # @UAF Module EFI/Tiano Decompression
    if is_comp and is_efi_compressed(uaf_data_raw, False):
        dec_fname = uaf_fname.replace('.temp', uaf_fext) # Decompressed @UAF Module file path
        
        if efi_decompress(uaf_fname, dec_fname, padding + 4) == 0:
            with open(dec_fname, 'rb') as dec: uaf_data_raw = dec.read() # Read back the @UAF Module decompressed Raw data
            
            os.remove(uaf_fname) # Successful decompression, delete compressed @UAF Module file
            
            uaf_fname = dec_fname # Adjust @UAF Module file path to the decompressed one
    
    # Process and Print known text only @UAF Modules (after EFI/Tiano Decompression)
    if uaf_tag in UAF_TAG_DICT and UAF_TAG_DICT[uaf_tag][2] == 'Text':
        print('\n%s%s:' % (padder(padding + 4), UAF_TAG_DICT[uaf_tag][1]))
        print('\n%s%s' % (padder(padding + 8), uaf_data_raw.decode('utf-8','ignore')))
    
    # Parse Default Command Status @UAF Module (@DIS)
    if len(uaf_data_raw) and uaf_tag == '@DIS':
        dis_hdr = get_struct(uaf_data_raw, 0x0, DisHeader) # Parse @DIS Module Raw Header Structure
        
        print('\n%sDefault Command Status Header:\n' % padder(padding + 4))
        
        dis_hdr.struct_print(padding + 8) # Print @DIS Module Raw Header Info
        
        # Store/Save @DIS Module Header Info in file
        with open(uaf_fname[:-3] + 'txt', 'a', encoding='utf-8') as dis:
            with contextlib.redirect_stdout(dis):
                dis_hdr.struct_print(0) # Store @DIS Module Header Info
        
        dis_data = uaf_data_raw[DIS_HDR_LEN:] # @DIS Module Entries Data
        
        # Parse all @DIS Module Entries
        for mod_idx in range(dis_hdr.EntryCount):
            dis_mod = get_struct(dis_data, mod_idx * DIS_MOD_LEN, DisModule) # Parse @DIS Module Raw Entry Structure
            
            print('\n%sDefault Command Status Entry %0.2d/%0.2d:\n' % (padder(padding + 8), mod_idx + 1, dis_hdr.EntryCount))
            
            dis_mod.struct_print(padding + 12) # Print @DIS Module Raw Entry Info
            
            # Store/Save @DIS Module Entry Info in file
            with open(uaf_fname[:-3] + 'txt', 'a', encoding='utf-8') as dis:
                with contextlib.redirect_stdout(dis):
                    print()
                    dis_mod.struct_print(4) # Store @DIS Module Entry Info
        
        os.remove(uaf_fname) # Delete @DIS Module binary, info exported as text
    
    # Parse Name|Non-AMI List (?) @UAF Module (@NAL)
    if len(uaf_data_raw) >= 5 and (uaf_tag,uaf_data_raw[0],uaf_data_raw[4]) == ('@NAL',0x40,0x3A):
        nal_info = uaf_data_raw.decode('utf-8','ignore').replace('\r','').strip().split('\n')
        
        print('\n%s@UAF Module Name List:\n' % padder(padding + 4))
        
        # Parse all @NAL Module Entries
        for info in nal_info:
            info_tag,info_val = info.split(':',1)
            
            print('%s%s : %s' % (padder(padding + 8), info_tag, info_val)) # Print @NAL Module Tag-Path Info
            
            nal_dict[info_tag] = os.path.basename(info_val) # Assign a file name (w/o path) to each Tag
    
    # Parse Insyde BIOS @UAF Module (@INS)
    if uaf_tag == '@INS' and is_7z_supported(uaf_fname):
        ins_dir = os.path.join(extract_path, safe_name(uaf_tag + '_nested-SFX')) # Generate extraction directory
        
        print('\n%sInsyde BIOS 7z SFX Archive:' % padder(padding + 4))
        
        if a7z_decompress(uaf_fname, ins_dir, '7z SFX', padding + 8) == 0:
            os.remove(uaf_fname) # Successful extraction, delete @INS Module file/archive
    
    # Detect & Unpack AMI BIOS Guard (PFAT) BIOS image
    pfat_match,pfat_buffer = get_ami_pfat(uaf_data_raw)
    
    if pfat_match:
        pfat_dir = os.path.join(extract_path, safe_name(uaf_name))
        
        parse_pfat_file(pfat_buffer, pfat_dir, padding + 4)
        
        os.remove(uaf_fname) # Delete PFAT Module file after extraction
    
    # Detect Intel Engine firmware image and show ME Analyzer advice
    if uaf_tag.startswith('@ME') and PAT_INTEL_ENG.search(uaf_data_raw):
        print('\n%sIntel Management Engine (ME) Firmware:\n' % padder(padding + 4))
        print('%sUse "ME Analyzer" from https://github.com/platomav/MEAnalyzer' % padder(padding + 8))
    
    # Get best Nested AMI UCP Pattern match based on @UAF Size
    nested_uaf_off,nested_uaf_bin = get_ami_ucp(uaf_data_raw)
    
    # Parse Nested AMI UCP Structure
    if nested_uaf_off:
        uaf_dir = os.path.join(extract_path, safe_name(uaf_tag + '_nested-UCP')) # Generate extraction directory
        
        ucp_extract(nested_uaf_bin, uaf_dir, padding + 4, is_chk16) # Call recursively
        
        os.remove(uaf_fname) # Delete raw nested AMI UCP Structure after successful recursion/extraction
    
    return nal_dict

# Get common ctypes Structure Sizes
UAF_HDR_LEN = ctypes.sizeof(UafHeader)
UAF_MOD_LEN = ctypes.sizeof(UafModule)
DIS_HDR_LEN = ctypes.sizeof(DisHeader)
DIS_MOD_LEN = ctypes.sizeof(DisModule)
UII_HDR_LEN = ctypes.sizeof(UiiHeader)

# AMI UCP Tag Dictionary
UAF_TAG_DICT = {
    '@3FI' : ['HpBiosUpdate32.efi', '', ''],
    '@3S2' : ['HpBiosUpdate32.s12', '', ''],
    '@3S4' : ['HpBiosUpdate32.s14', '', ''],
    '@3S9' : ['HpBiosUpdate32.s09', '', ''],
    '@3SG' : ['HpBiosUpdate32.sig', '', ''],
    '@AMI' : ['UCP_Nested.bin', 'Nested AMI UCP', ''],
    '@B12' : ['BiosMgmt.s12', '', ''],
    '@B14' : ['BiosMgmt.s14', '', ''],
    '@B32' : ['BiosMgmt32.s12', '', ''],
    '@B34' : ['BiosMgmt32.s14', '', ''],
    '@B39' : ['BiosMgmt32.s09', '', ''],
    '@B3E' : ['BiosMgmt32.efi', '', ''],
    '@BM9' : ['BiosMgmt.s09', '', ''],
    '@BME' : ['BiosMgmt.efi', '', ''],
    '@CKV' : ['Check_Version.txt', 'Check Version', 'Text'],
    '@CMD' : ['AFU_Command.txt', 'AMI AFU Command', 'Text'],
    '@CPM' : ['AC_Message.txt', 'Confirm Power Message', ''],
    '@DCT' : ['DevCon32.exe', 'Device Console WIN32', ''],
    '@DCX' : ['DevCon64.exe', 'Device Console WIN64', ''],
    '@DFE' : ['HpDevFwUpdate.efi', '', ''],
    '@DFS' : ['HpDevFwUpdate.s12', '', ''],
    '@DIS' : ['Command_Status.bin', 'Default Command Status', ''],
    '@ENB' : ['ENBG64.exe', '', ''],
    '@INS' : ['Insyde_Nested.bin', 'Nested Insyde SFX', ''],
    '@M32' : ['HpBiosMgmt32.s12', '', ''],
    '@M34' : ['HpBiosMgmt32.s14', '', ''],
    '@M39' : ['HpBiosMgmt32.s09', '', ''],
    '@M3I' : ['HpBiosMgmt32.efi', '', ''],
    '@MEC' : ['FWUpdLcl.txt', 'Intel FWUpdLcl Command', 'Text'],
    '@MED' : ['FWUpdLcl_DOS.exe', 'Intel FWUpdLcl DOS', ''],
    '@MET' : ['FWUpdLcl_WIN32.exe', 'Intel FWUpdLcl WIN32', ''],
    '@MFI' : ['HpBiosMgmt.efi', '', ''],
    '@MS2' : ['HpBiosMgmt.s12', '', ''],
    '@MS4' : ['HpBiosMgmt.s14', '', ''],
    '@MS9' : ['HpBiosMgmt.s09', '', ''],
    '@NAL' : ['UAF_List.txt', 'Name List', ''],
    '@OKM' : ['OK_Message.txt', 'OK Message', ''],
    '@PFC' : ['BGT_Command.txt', 'AMI BGT Command', 'Text'],
    '@R3I' : ['CryptRSA32.efi', '', ''],
    '@RFI' : ['CryptRSA.efi', '', ''],
    '@UAF' : ['UCP_Main.bin', 'Utility Auxiliary File', ''],
    '@UFI' : ['HpBiosUpdate.efi', '', ''],
    '@UII' : ['UCP_Info.txt', 'Utility Identification Information', ''],
    '@US2' : ['HpBiosUpdate.s12', '', ''],
    '@US4' : ['HpBiosUpdate.s14', '', ''],
    '@US9' : ['HpBiosUpdate.s09', '', ''],
    '@USG' : ['HpBiosUpdate.sig', '', ''],
    '@VER' : ['OEM_Version.txt', 'OEM Version', 'Text'],
    '@VXD' : ['amifldrv.vxd', '', ''],
    '@W32' : ['amifldrv32.sys', '', ''],
    '@W64' : ['amifldrv64.sys', '', ''],
    }

if __name__ == '__main__':
    # Show script title
    show_title(title)
    
    # Set argparse Arguments    
    argparser = argparse_init()
    argparser.add_argument('-c', '--checksum', help='verify AMI UCP Checksums (slow)', action='store_true')
    arguments = argparser.parse_args()
    
    # Pretty Python exception handler (must be after argparse)
    sys.excepthook = nice_exc_handler
    
    # Check Python Version (must be after argparse)
    check_sys_py()
    
    # Check OS Platform (must be after argparse)
    check_sys_os()
    
    # Process input files and generate output path
    input_files,output_path = process_input_files(arguments, sys.argv)
    
    # Initial output padding count
    padding = 4
    
    for input_file in input_files:
        input_name = os.path.basename(input_file)
        
        print('\n*** %s' % input_name)
        
        with open(input_file, 'rb') as in_file: input_buffer = in_file.read()
        
        # Get best AMI UCP Pattern match based on @UAF Size
        main_uaf_off,main_uaf_bin = get_ami_ucp(input_buffer)
        
        if not main_uaf_off:
            print('\n%sError: This is not an AMI UCP BIOS executable!' % padder(padding))
            
            continue # Next input file
        
        extract_path = os.path.join(output_path, input_name)
        
        ucp_extract(main_uaf_bin, extract_path, padding, arguments.checksum)
    
        print('\n%sExtracted AMI UCP BIOS executable!' % padder(padding))
    
    print_input('\nDone!')
