from mvp5.models import Config,Rule
def spi_example():
    return Config(spi_enabled=1,spi_clk=2,spi_mosi=3,spi_cs=4,rules=(
        Rule(action=2,min_ticks=25000,max_ticks=100000),
        Rule(max_ticks=25000),
        Rule(target_channel=0,target_kind=1,spi_value=0xc3,spi_mask=0xff),
        Rule(target_channel=0,target_kind=1,spi_value=0x3c,spi_mask=0xff)))
