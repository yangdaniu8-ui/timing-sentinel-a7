/* Workload candidate: no fixed-delay response scenarios. HSI is NOT calibrated. */
#include "stm32f10x.h"
#include "workload.h"
uint32_t SystemCoreClock=8000000u;
struct workload_debug_state {
    uint32_t magic,version,size,id,profile,phase,completed,state,error;
    uint32_t expected,actual,task_cycles,elapsed_us,irq_count,irq_cycles;
    uint32_t irq_max_cycles,irq_digest,period_us,timer_pending,ready_low_seen;
};
volatile struct workload_debug_state workload_debug;
typedef char debug_size_guard[(sizeof(struct workload_debug_state)==80u)?1:-1];
static uint16_t task_input[WL_SAMPLES];
static volatile uint32_t irq_count,irq_cycles,irq_max_cycles,irq_digest;
static void hardware_init(void) {
    RCC->CR |= RCC_CR_HSION;
    while (!(RCC->CR & RCC_CR_HSIRDY)) {}
    RCC->CFGR &= ~RCC_CFGR_SW;
    while ((RCC->CFGR & RCC_CFGR_SWS)!=RCC_CFGR_SWS_HSI) {}
    RCC->CFGR &= ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2);
    RCC->CR &= ~(RCC_CR_PLLON | RCC_CR_HSEON | RCC_CR_CSSON);
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN |
                     RCC_APB2ENR_IOPCEN | RCC_APB2ENR_USART1EN | RCC_APB2ENR_SPI1EN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM2EN | RCC_APB1ENR_TIM3EN;
    (void)RCC->APB2ENR;
    GPIOA->BSRR=(1u<<9)|(1u<<(8+16)); /* TX high, READY pull-down */
    GPIOA->CRH=(GPIOA->CRH & ~0xffu) | 0xb8u; /* PA9 AF PP 50 MHz; PA8 IPD */
    GPIOA->BSRR=(1u<<4)|(1u<<(5+16))|(1u<<(7+16)); /* CS high, SCK/MOSI low */
    /* Use 2 MHz AF output drivers for the 250 kHz bus, rather than 50 MHz
     * drivers on long jumper wires. Do not filter faults in the FPGA monitor. */
    GPIOA->CRL=(GPIOA->CRL & ~0xf0ff0000u) | 0xa0a20000u; /* PA4 PP, PA5/7 AF PP */
    SPI1->CR1=SPI_CR1_MSTR | SPI_CR1_SSM | SPI_CR1_SSI | SPI_CR1_BR_2;
    SPI1->CR2=0;SPI1->CR1 |= SPI_CR1_SPE; /* mode0, MSB, 8MHz/32 =250kHz */
    GPIOB->BSRR=1u<<16;
    GPIOB->CRL=(GPIOB->CRL & ~0xfu) | 2u; /* PB0 PP, 2 MHz */
    GPIOC->BSRR=1u<<13;
    GPIOC->CRH=(GPIOC->CRH & ~(0xfu<<20)) | (2u<<20); /* PC13 LED */
    USART1->CR1=0; USART1->CR2=0; USART1->CR3=0;
    USART1->BRR=69; /* HSI nominal 8 MHz / 69 = 115942 baud (+0.64%). */
    USART1->CR1=USART_CR1_UE | USART_CR1_TE;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT=0; DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    NVIC_SetPriority(TIM3_IRQn,2); NVIC_DisableIRQ(TIM3_IRQn);
    TIM3->CR1=0; TIM3->DIER=0; TIM3->PSC=7;
    TIM2->CR1=0; TIM2->PSC=7; TIM2->ARR=0xffff; TIM2->EGR=TIM_EGR_UG;
    TIM2->SR=0; TIM2->CR1=TIM_CR1_CEN; /* nominal 1 us, wraps 65.536 ms */
}
static void send_byte(uint8_t value) {
    while (!(USART1->SR & USART_SR_TXE)) {}
    USART1->DR=value;
    while (!(USART1->SR & USART_SR_TC)) {}
}
static void delay_us(uint16_t us) {
    uint16_t start=(uint16_t)TIM2->CNT;
    while ((uint16_t)((uint16_t)TIM2->CNT-start)<us) {}
}

void TIM3_IRQHandler(void) {
    uint32_t start=DWT->CYCCNT,elapsed;
    if(!(TIM3->SR & TIM_SR_UIF)) return;
    TIM3->SR=0;
    irq_digest=workload_irq_step(irq_digest);
    elapsed=DWT->CYCCNT-start;
    irq_count++; irq_cycles+=elapsed;
    if(elapsed>irq_max_cycles) irq_max_cycles=elapsed;
}
static void send_spi(uint8_t value) {
    GPIOA->BSRR=1u<<(4+16); delay_us(5);
    while(!(SPI1->SR & SPI_SR_TXE)) {}
    *(volatile uint8_t *)&SPI1->DR=value;
    while(!(SPI1->SR & SPI_SR_RXNE)) {}
    (void)SPI1->DR;
    while(SPI1->SR & SPI_SR_BSY) {}
    delay_us(5); GPIOA->BSRR=1u<<4;
}
static void run_job(uint32_t id) {
    uint32_t profile=workload_profile(id),phase=(id*37u)&255u;
    uint32_t period=workload_period(profile),i,start,result,elapsed,start_us;
    uint8_t packet[10];
    GPIOB->BSRR=1u<<16;
    irq_count=irq_cycles=irq_max_cycles=0; irq_digest=0x12345678u;
    TIM3->CR1=0; TIM3->DIER=0; NVIC_DisableIRQ(TIM3_IRQn);
    if(period) {
        TIM3->ARR=period-1u; TIM3->EGR=TIM_EGR_UG;
        TIM3->CNT=phase%period; TIM3->SR=0;
        NVIC_ClearPendingIRQ(TIM3_IRQn);
    }
    workload_debug.id=id; workload_debug.profile=profile; workload_debug.phase=phase;
    workload_debug.state=2u; workload_debug.period_us=period;
    workload_marker(id,profile,phase,packet);
    for(i=0;i<10u;i++) send_byte(packet[i]);
    /* A on the FPGA is UART stop-bit center, a few us before TX TC.
     * Measured A->GPIO also includes identical entry/check/IRQ-stop overhead. */
    start_us=(uint16_t)TIM2->CNT; start=DWT->CYCCNT;
    if(period) { TIM3->DIER=TIM_DIER_UIE; NVIC_EnableIRQ(TIM3_IRQn); TIM3->CR1=TIM_CR1_CEN; }
    result=workload_compute(task_input);
    TIM3->CR1=0; TIM3->DIER=0; NVIC_DisableIRQ(TIM3_IRQn);
    elapsed=DWT->CYCCNT-start;
    workload_debug.timer_pending=(TIM3->SR & TIM_SR_UIF)!=0;
    TIM3->SR=0; NVIC_ClearPendingIRQ(TIM3_IRQn);
    workload_debug.actual=result;
    workload_debug.error=(result!=0x8065d77u) ? 1u : 0u;
    if(period && (irq_max_cycles+128u)>=period*8u) workload_debug.error|=2u;
    /* Only verified computed output emits successful completion, not a delay. */
    if(!workload_debug.error) { GPIOB->BSRR=1u; send_spi(0xc3u); }
    else send_spi(0xeeu);
    workload_debug.task_cycles=elapsed;
    workload_debug.elapsed_us=(uint16_t)((uint16_t)TIM2->CNT-start_us);
    workload_debug.irq_count=irq_count; workload_debug.irq_cycles=irq_cycles;
    workload_debug.irq_max_cycles=irq_max_cycles; workload_debug.irq_digest=irq_digest;
    workload_debug.completed=id; workload_debug.state=3u;
    GPIOC->BSRR=(id&1u) ? (1u<<(13+16)) : (1u<<13);
}
int main(void) {
    uint32_t id=0,low_seen=0,stable=0; uint16_t since=0;
    hardware_init(); workload_input(task_input);
    workload_debug.magic=WL_MAGIC; workload_debug.version=1;
    workload_debug.size=sizeof(workload_debug);
    workload_debug.expected=0x8065d77u; workload_debug.state=1u;
    for(;;) {
        uint32_t ready=(GPIOA->IDR & (1u<<8))!=0;
        if(!ready) { low_seen=1; stable=0; workload_debug.ready_low_seen=1; }
        else if(low_seen && id<4095u) {
            if(!stable) { stable=1; since=(uint16_t)TIM2->CNT; }
            else if((uint16_t)((uint16_t)TIM2->CNT-since)>=1000u) {
                low_seen=0; stable=0; workload_debug.ready_low_seen=0;
                run_job(++id);
                /* Quiet hold/return-to-zero outside the measured computation.
                 * FPGA finishes post-window before it can be armed again. */
                delay_us(1000); GPIOB->BSRR=1u<<16;
            }
        }
    }
}
