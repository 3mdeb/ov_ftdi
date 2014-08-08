#!/usr/bin/python3.3

# This needs python3.3 or greater - argparse changes behavior
# TODO - workaround

import LibOV
import argparse
import time

import zipfile

import sys
import os
import struct
#import yappi

def as_ascii(arg):
    if arg == None:
        return None
    return arg.encode('ascii')

class Command:
    def __subclasshook__(self):
        pass

    @staticmethod
    def setup_args(sp):
        pass

__cmd_keeper = []
def command(_name, *_args):
    def _i(todeco):
        class _sub(Command):
            name = _name

            @staticmethod
            def setup_args(sp):
                for (name, typ, *default) in _args:
                    if len(default):
                            name = "--" + name
                            default = default[0]
                    else:
                        default = None
                    sp.add_argument(name, type=typ, default=default)

            @staticmethod
            def go(dev, args):
                aarray = dict([(i, getattr(args, i)) for (i, *_) in _args])
                todeco(dev, **aarray)
        __cmd_keeper.append(_sub)
        return todeco

    return _i

int16 = lambda x: int(x, 16)


def check_ulpi_clk(dev):
    clks_up = dev.regs.ucfg_stat.rd()

    if not clks_up:
        print("ULPI Clock has not started up - osc?")
        return 1

    return 0

@command('uwrite', ('addr', str), ('val', int16))
def uwrite(dev, addr, val):
    addr = int(addr, 16)

    if check_ulpi_clk(dev):
        return 

    dev.ulpiwrite(addr, val)

@command('uread', ('addr', str))
def uread(dev, addr):
    addr = int(addr, 16)

    if check_ulpi_clk(dev):
        return 

    print ("ULPI %02x: %02x" % (addr, dev.ulpiread(addr)))

@command('report')
def report(dev):

    print("USB PHY Tests")
    if check_ulpi_clk(dev):
        print("\tWARNING: ULPI PHY clock not started; skipping ULPI tests")
    else:
        # display the ULPI identifier
        ident = 0
        for x in [dev.ulpiregs.vidh,
                dev.ulpiregs.vidl,
                dev.ulpiregs.pidh,
                dev.ulpiregs.pidl]:
            ident <<= 8
            ident |= x.rd()

        name = 'unknown'
        if ident == LibOV.SMSC_334x_MAGIC:
            name = 'SMSC 334x'
        print("\tULPI PHY ID: %08x (%s)" % (ident, name))

        # do in depth phy tests
        if ident == LibOV.SMSC_334x_MAGIC:
            dev.ulpiregs.scratch.wr(0)
            dev.ulpiregs.scratch_set.wr(0xCF)
            dev.ulpiregs.scratch_clr.wr(0x3C)

            stat = "OK" if dev.ulpiregs.scratch.rd() == 0xC3 else "FAIL"

            print("\tULPI Scratch register IO test: %s" % stat)
            print("\tPHY Function Control Reg:  %02x" % dev.ulpiregs.func_ctl.rd())
            print("\tPHY Interface Control Reg: %02x" % dev.ulpiregs.intf_ctl.rd())
        else:
            print("\tUnknown PHY - skipping phy tests")

    print ("SDRAM tests")
    def cb(n, ok):
        print("\t... %d: %s" % (n, "OK" if ok else "FAIL"))
    stat = do_sdramtests(dev, cb)
    if stat == -1:
        print("\t... all passed")


class OutputCustom:
    def __init__(self, output):
        self.output = output

    def handle_usb(self, pkt, flags):
        pkthex = " ".join("%02x" % x for x in pkt)
        self.output.write("data=%s speed=%s\n" % (pkthex, speed.upper()))


class OutputPcap:
    LINK_TYPE = 255 #FIXME

    def __init__(self, output):
        self.output = output
        self.output.write(struct.pack("IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 1<<20, self.LINK_TYPE))

    def handle_usb(self, pkt, flags):
        self.output.write(struct.pack("IIIIH", 0, 0, len(pkt) + 2, len(pkt) + 2, flags))
        self.output.write(pkt)

def do_sdramtests(dev, cb=None, tests = range(0, 6)):
    
    for i in tests:
        dev.regs.SDRAM_TEST_CMD.wr(0x80 | i)
        stat = 0x40
        while (stat & 0x40):
            time.sleep(0.1)
            stat = dev.regs.SDRAM_TEST_CMD.rd() 

        ok = stat & 0x20
        if cb is not None:
            cb(i, ok)

        if not ok:
            return i
    else:
        return -1

@command('sdramtest')
def sdramtest(dev):
    # LEDS select
    dev.regs.LEDS_MUX_0.wr(1)

    stat = do_sdramtests(dev, tests = [3])
    if stat != -1:
        print("SDRAM test failed on test %d\n" % stat)
    else:
        print("SDRAM test passed")

    dev.regs.LEDS_MUX_0.wr(0)

@command('sniff', ('speed', str), ('format', str, 'verbose'), ('out', str, None), ('timeout', int, None))
def sniff(dev, speed, format, out, timeout):
    # LEDs off
    dev.regs.LEDS_MUX_2.wr(0)
    dev.regs.LEDS_OUT.wr(0)

    # LEDS 0/1 to FTDI TX/RX
    dev.regs.LEDS_MUX_0.wr(2)
    dev.regs.LEDS_MUX_1.wr(2)

    assert speed in ["hs", "fs", "ls"]

    if check_ulpi_clk(dev):
        return

    # set to non-drive; set FS or HS as requested
    if speed == "hs":
            dev.ulpiregs.func_ctl.wr(0x48)
            dev.rxcsniff.service.highspeed = True
    elif speed == "fs":
            dev.ulpiregs.func_ctl.wr(0x49)
            dev.rxcsniff.service.highspeed = False
    elif speed == "ls":
            dev.ulpiregs.func_ctl.wr(0x4a)
            dev.rxcsniff.service.highspeed = False
    else:
        assert 0,"Invalid Speed"

    assert format in ["verbose", "custom", "pcap"]

    output_handler = None
    out = out and open(out, "wb")

    if format == "custom":
        output_handler = OutputCustom(out or sys.stdout)
    elif format == "pcap":
        assert out, "can't output pcap to stdout, use --out"
        output_handler = OutputPcap(out)

    if output_handler is not None:
      dev.rxcsniff.service.handlers = [output_handler.handle_usb]

    elapsed_time = 0
    try:
        dev.regs.CSTREAM_CFG.wr(1)
        while 1:
            if timeout and elapsed_time > timeout:
                break
            time.sleep(1)
            elapsed_time = elapsed_time + 1
    except KeyboardInterrupt:
        pass
    finally:
        dev.regs.CSTREAM_CFG.wr(0)

    if out is not None:
        out.close()

@command('debug-stream')
def debug_stream(dev):
    cons = dev.regs.CSTREAM_CONS_LO.rd() | dev.regs.CSTREAM_CONS_HI.rd() << 8
    prod_hd = dev.regs.CSTREAM_PROD_HD_LO.rd() | dev.regs.CSTREAM_PROD_HD_HI.rd() << 8
    prod = dev.regs.CSTREAM_PROD_LO.rd() | dev.regs.CSTREAM_PROD_HI.rd() << 8
    size = dev.regs.CSTREAM_SIZE_LO.rd() | dev.regs.CSTREAM_SIZE_HI.rd() << 8

    state = dev.regs.CSTREAM_PROD_STATE.rd()

    laststart = dev.regs.CSTREAM_LAST_START_LO.rd() | dev.regs.CSTREAM_LAST_START_HI.rd() << 8
    lastcount = dev.regs.CSTREAM_LAST_COUNT_LO.rd() | dev.regs.CSTREAM_LAST_COUNT_HI.rd() << 8
    lastpw = dev.regs.CSTREAM_LAST_PW_LO.rd() | dev.regs.CSTREAM_LAST_PW_HI.rd() << 8

    print("cons: %04x prod-wr: %04x prod-hd: %04x size: %04x state: %02x" % (cons, prod, prod_hd, size, state))
    print("\tlaststart: %04x lastcount: %04x (end: %04x) pw-at-write: %04x" % (laststart, lastcount, laststart + lastcount, lastpw))

@command('ioread', ('addr', str))
def ioread(dev, addr):
    print("%s: %02x" % (addr, dev.ioread(addr)))

@command('iowrite', ('addr', str), ('value', int16))
def iowrite(dev, addr, value):
    dev.iowrite(addr, value)

@command('led-test', ('v', int16))
def ledtest(dev, v):
    dev.regs.leds_out.wr(v)

@command('eep-erase')
def eeperase(dev):
    dev.dev.eeprom_erase()

@command('eep-program', ('serialno', int))
def eepprogram(dev, serialno):
    dev.dev.eeprom_program(serialno)

@command('sdram_host_read_test')
def sdram_host_read_test(dev):
    dev.regs.SDRAM_HOST_READ_RPTR.wr(0)

    cnt = 0
    while True:
        rptr = dev.regs.SDRAM_HOST_READ_RPTR_STATUS.rd()
        cnt += 1
        if cnt == 10:
            print("GO")
            dev.regs.SDRAM_HOST_READ_GO.wr(1)

        print("rptr = %08x i_stb=%08x i_ack=%08x d_stb=%08x d_term=%08x s0=%08x s1=%08x s2=%08x" % (
            rptr,
            dev.regs.SDRAM_HOST_READ_DEBUG_I_STB.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_I_ACK.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_D_STB.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_D_TERM.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_S0.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_S1.rd(),
            dev.regs.SDRAM_HOST_READ_DEBUG_S2.rd()))

        if cnt == 20:
            print("STOP")
            dev.regs.SDRAM_HOST_READ_GO.wr(0)
#            print("STOP: %d" % dev.regs.SDRAM_HOST_READ_GO.rd())


class LB_Test(Command):
    name = "lb-test"

    @staticmethod
    def setup_args(sp):
        sp.add_argument("size", type=int, default=64, nargs='?')

    @staticmethod
    def go(dev, args):
        # Stop the generator - do twice to make sure
        # theres no hanging packet 
        dev.regs.RANDTEST_CFG.wr(0)
        dev.regs.RANDTEST_CFG.wr(0)

        # LEDs off
        dev.regs.LEDS_MUX_2.wr(0)
        dev.regs.LEDS_OUT.wr(0)

        # LEDS 0/1 to FTDI TX/RX
        dev.regs.LEDS_MUX_0.wr(2)
        dev.regs.LEDS_MUX_1.wr(2)

        # Set test packet size
        dev.regs.RANDTEST_SIZE.wr(args.size)

        # Reset the statistics counters
        dev.lfsrtest.reset()

        # Start the test (and reinit the generator)
        dev.regs.RANDTEST_CFG.wr(1)

        st = time.time()
        try:
            while 1:
                time.sleep(1)
                b = dev.lfsrtest.stats()
                print("%4s %20d bytes %f MB/sec average" % (
                    "ERR" if b.error else "OK", 
                    b.total, b.total/float(time.time() - st)/1024/1024))

        except KeyboardInterrupt:
            dev.regs.randtest_cfg.wr(0)


def main():

    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", "-p", type=lambda x: zipfile.ZipFile(x, 'r'), 
            default=os.getenv('OV_PKG'))
    ap.add_argument("-l", "--load", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--config-only", "-C", action="store_true")

    # Bind commands
    subparsers = ap.add_subparsers()
    for i in Command.__subclasses__():
        sp = subparsers.add_parser(i.name)
        i.setup_args(sp)
        sp.set_defaults(hdlr=i)

    args = ap.parse_args()


    dev = LibOV.OVDevice(mapfile=args.pkg.open('map.txt', 'r'), verbose=args.verbose)

    err = dev.open(bitstream=args.pkg.open('ov3.bit', 'r') if args.load else None)

    if err:
        if err == -4:
            print("USB: Unable to find device")
            return 1
        print("USB: Error opening device (1)\n")
        print(err)

    if not dev.isLoaded():
        print("FPGA not loaded, forcing reload")
        dev.close()

        err = dev.open(bitstream=args.pkg.open('ov3.bit','r'))

    if err:
        print("USB: Error opening device (2)\n")
        return 1


    if args.config_only:
        return

    dev.dev.write(LibOV.FTDI_INTERFACE_A, b'\x00' * 512, async=False)

    try:
        if hasattr(args, 'hdlr'):
            args.hdlr.go(dev, args)
    finally:
        dev.close()

if  __name__ == "__main__":
#    yappi.start()
    main()
#    yappi.print_stats()

