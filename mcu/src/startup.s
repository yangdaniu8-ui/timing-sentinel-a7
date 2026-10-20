        PRESERVE8
        THUMB
        AREA STACK, NOINIT, READWRITE, ALIGN=3
Stack_Mem SPACE 2048
__initial_sp
        AREA HEAP, NOINIT, READWRITE, ALIGN=3
__heap_base
__heap_limit
        EXPORT __initial_sp
        EXPORT __heap_base
        EXPORT __heap_limit
        AREA RESET, DATA, READONLY
        EXPORT __Vectors
        IMPORT TIM3_IRQHandler
__Vectors
        DCD __initial_sp
        DCD Reset_Handler
        DCD Default_Handler, Default_Handler, Default_Handler, Default_Handler
        DCD Default_Handler, 0, 0, 0, 0, Default_Handler
        DCD Default_Handler, 0, Default_Handler, Default_Handler
        DCD Default_Handler ; IRQ 0
        DCD Default_Handler ; IRQ 1
        DCD Default_Handler ; IRQ 2
        DCD Default_Handler ; IRQ 3
        DCD Default_Handler ; IRQ 4
        DCD Default_Handler ; IRQ 5
        DCD Default_Handler ; IRQ 6
        DCD Default_Handler ; IRQ 7
        DCD Default_Handler ; IRQ 8
        DCD Default_Handler ; IRQ 9
        DCD Default_Handler ; IRQ 10
        DCD Default_Handler ; IRQ 11
        DCD Default_Handler ; IRQ 12
        DCD Default_Handler ; IRQ 13
        DCD Default_Handler ; IRQ 14
        DCD Default_Handler ; IRQ 15
        DCD Default_Handler ; IRQ 16
        DCD Default_Handler ; IRQ 17
        DCD Default_Handler ; IRQ 18
        DCD Default_Handler ; IRQ 19
        DCD Default_Handler ; IRQ 20
        DCD Default_Handler ; IRQ 21
        DCD Default_Handler ; IRQ 22
        DCD Default_Handler ; IRQ 23
        DCD Default_Handler ; IRQ 24
        DCD Default_Handler ; IRQ 25
        DCD Default_Handler ; IRQ 26
        DCD Default_Handler ; IRQ 27
        DCD Default_Handler ; IRQ 28
        DCD TIM3_IRQHandler ; IRQ 29
        DCD Default_Handler ; IRQ 30
        DCD Default_Handler ; IRQ 31
        DCD Default_Handler ; IRQ 32
        DCD Default_Handler ; IRQ 33
        DCD Default_Handler ; IRQ 34
        DCD Default_Handler ; IRQ 35
        DCD Default_Handler ; IRQ 36
        DCD Default_Handler ; IRQ 37
        DCD Default_Handler ; IRQ 38
        DCD Default_Handler ; IRQ 39
        DCD Default_Handler ; IRQ 40
        DCD Default_Handler ; IRQ 41
        DCD Default_Handler ; IRQ 42
        AREA |.text|, CODE, READONLY
        EXPORT Reset_Handler
Reset_Handler PROC
        IMPORT __main
        LDR R0, =__main
        BX R0
        ENDP
Default_Handler PROC
        B Default_Handler
        ENDP
        END
