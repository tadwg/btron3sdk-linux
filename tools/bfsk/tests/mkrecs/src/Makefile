# SPDX-License-Identifier: MIT
#
#	Makefile (mkrecs)  bfsk の検証用 CLI コマンド
#	(BTRON SDK の util/sample/src/Makefile をもとに作成)
#
DEPS = Dependencies
DEPENDENCIES_OUTPUT := $(DEPS)

include ../../etc/makerules

TARGET = mkrecs
S = ../src
VPATH = $S
HEADER := $(S) $(HEADER)
SRC := $(notdir $(wildcard $(TARGET:%=$S/%.*)))
OBJ	= $(addsuffix .o, $(basename $(SRC)))
WC_SRC	= $(filter %.C, $(SRC))
LOADLIBES = -lapp
CFLAGS += -Wall

.PHONY: all clean
ALL = $(TARGET) $(addsuffix .map, $(TARGET))
all: $(ALL)
clean:
	$(RM) $(OBJ) $(ALL) $(DEPS)

ifdef DEPENDENCIES_OUTPUT
  $(DEPS): ; touch $(DEPS)
else
  $(DEPS): $(SRC) ; $(MAKEDEPS) $@ $?
endif
include $(DEPS)
