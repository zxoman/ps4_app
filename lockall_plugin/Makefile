# lockall - GoldHEN plugin Makefile
#
# Based on the official GoldHEN_Plugins plugin_template Makefile.
# The GoldHEN SDK is compiled from source into this module (as recommended
# by the SDK README) so no separately-built libGoldHEN_Hook.a is required.
#
# Required environment:
#   OO_PS4_TOOLCHAIN  path to the OpenOrbis PS4 Toolchain
#   GOLDHEN_SDK       path to the cloned GoldHEN_Plugins_SDK repo
#
# Example:
#   export OO_PS4_TOOLCHAIN=$HOME/openorbis
#   export GOLDHEN_SDK=$HOME/GoldHEN_Plugins_SDK
#   make

TARGET_NAME := lockall

DEBUG_FLAGS := -D__FINAL__=1
LOG_TYPE    := -D__USE_PRINTF__
BUILD_TYPE  := _final

ifeq ($(DEBUG),1)
    DEBUG_FLAGS = -D__FINAL__=0
    BUILD_TYPE  = _debug
endif

# Root vars
TOOLCHAIN  := $(OO_PS4_TOOLCHAIN)
GH_SDK     := $(GOLDHEN_SDK)
PROJDIR    := source
INTDIR     := build
COMMON_DIR := $(GH_SDK)/source

# SDK sources compiled into the module
SDK_CFILES := $(COMMON_DIR)/GoldHEN.c $(COMMON_DIR)/Syscall.c

# Project sources
CFILES := $(wildcard $(PROJDIR)/*.c)

OBJS      := $(patsubst $(PROJDIR)/%.c,$(INTDIR)/%.o,$(CFILES)) \
             $(patsubst $(COMMON_DIR)/%.c,$(INTDIR)/sdk_%.o,$(SDK_CFILES))
CRT_OBJ   := $(INTDIR)/crtprx.o
ELF       := $(INTDIR)/$(TARGET_NAME).elf
OELF      := $(INTDIR)/$(TARGET_NAME).oelf
PRX       := $(INTDIR)/$(TARGET_NAME).prx

LIBS := -lSceLibcInternal -lkernel -lSceSysmodule

EXTRAFLAGS := $(DEBUG_FLAGS) $(LOG_TYPE) -Wall

CFLAGS := $(EXTRAFLAGS) --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -c \
          -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include \
          -I$(GH_SDK)/include -I$(PROJDIR)

LDFLAGS := -m elf_x86_64 -pie --script $(TOOLCHAIN)/link.x -e _init \
           --eh-frame-hdr -L$(TOOLCHAIN)/lib $(LIBS)

_unused := $(shell mkdir -p $(INTDIR))

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Linux)
    CC := clang
    LD := ld.lld
    CDIR := linux
endif
ifeq ($(UNAME_S),Darwin)
    CC := /usr/local/opt/llvm/bin/clang
    LD := /usr/local/opt/llvm/bin/ld.lld
    CDIR := macos
endif

.PHONY: all clean
.DEFAULT_GOAL := all

all: check $(PRX)

check:
	@test -n "$(TOOLCHAIN)" || (echo "ERROR: set OO_PS4_TOOLCHAIN"; exit 1)
	@test -d "$(GH_SDK)/include" || (echo "ERROR: set GOLDHEN_SDK"; exit 1)
	@test -f "$(TOOLCHAIN)/bin/$(CDIR)/create-fself" || (echo "ERROR: OpenOrbis toolchain not found at $(TOOLCHAIN)"; exit 1)

$(INTDIR)/crtprx.o: $(GH_SDK)/crt/crtprx.c
	$(CC) --target=x86_64-pc-linux-gnu -ffreestanding -nostdlib -fno-builtin -fPIC \
	      -isysroot $(TOOLCHAIN) -isystem $(TOOLCHAIN)/include -c $< -o $@

$(INTDIR)/%.o: $(PROJDIR)/%.c
	$(CC) $(CFLAGS) -o $@ $<

$(INTDIR)/sdk_%.o: $(COMMON_DIR)/%.c
	$(CC) $(CFLAGS) -o $@ $<

$(PRX): $(OBJS) $(CRT_OBJ)
	$(LD) $(CRT_OBJ) $(OBJS) -o $(ELF) $(LDFLAGS)
	$(TOOLCHAIN)/bin/$(CDIR)/create-fself -in=$(ELF) -out=$(OELF) --lib=$(PRX) --paid 0x3800000000000011

clean:
	rm -rf $(INTDIR)
