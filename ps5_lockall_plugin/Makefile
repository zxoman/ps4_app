# lockall for PS5 / etaHEN
#
#   PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk make
#
# Produces build/lockall_ps5.plugin, ready for /data/etaHEN/plugins/.

PS5_PAYLOAD_SDK ?= $(HOME)/.cache/ps5build/ps5-payload-sdk
include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk

TID     ?= LOCK00001
VERSION ?= 1.00
OUT     := build
ELF     := $(OUT)/lockall_ps5.elf
PLUGIN  := $(OUT)/lockall_ps5.plugin
SRCS    := source/main.c

CFLAGS  := -Wall -O2 -g -DPS5
LDLIBS  := -lSceLibcInternal -lSceSystemService -lSceNet -lSceSysmodule \
           -lSceUserService -lSceNetCtl -lkernel_sys

all: $(PLUGIN)

$(OUT):
	mkdir -p $(OUT)

$(ELF): $(SRCS) | $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDLIBS)

$(PLUGIN): $(ELF) make_plugin.py
	python3 make_plugin.py $(ELF) $(TID) $(VERSION)

clean:
	rm -rf $(OUT)

.PHONY: all clean
