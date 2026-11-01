#
#	Makefile (kbpdsniff)
#	kbpd key map probe
#	GPL version (based on tadwg/virtualbox-additions)
#

DEPS = Dependencies
DEPENDENCIES_OUTPUT := $(DEPS)

# ドライバ標準ルール
include ../../etc/makerules

#----------------------------------------------------------------------
TARGET = kbpdsniff

S      = ../src
VPATH  = $(S)
HEADER := $(S) $(HEADER)

SRC    = kbpdsniff.c
OBJ    = $(addsuffix .o, $(basename $(SRC)))
WC_SRC = $(filter %.C, $(SRC))

ifneq ($(mode), debug)
  CFLAGS += -Wall -Wno-main
endif

#----------------------------------------------------------------------
.PHONY: all clean install

ALL = $(TARGET) $(TARGET).map

all: $(ALL)

$(TARGET): $(OBJ)
	$(LINK.o) $(LDOBJS) $^ $(LOADLIBES) $(LDLIBS) $(OUTPUT_OPTION)

clean:
	$(RM) $(OBJ) $(ALL) $(DEPS)

install: $(ALL:%=$(DRIVER_INSTALLDIR)/%)

$(ALL:%=$(DRIVER_INSTALLDIR)/%): $(DRIVER_INSTALLDIR)/%: %
	$(BD)/etc/backup_copy -t $< $(DRIVER_INSTALLDIR)

ifdef DEPENDENCIES_OUTPUT
  $(DEPS): ; touch $(DEPS)
else
  $(DEPS): $(SRC) ; $(MAKEDEPS) $@ $?
endif
include $(DEPS)
